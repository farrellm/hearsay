# Hearsay — Design Document

Status: draft, 2026-11-01. Nothing is built yet.

## 1. Overview

Hearsay is a personal podcast app: a PWA used mostly from an iPhone over Tailscale, backed by a
server on a home box. You subscribe to podcast RSS feeds. Hearsay downloads the episodes,
transcribes them with automatic speech recognition (ASR), and indexes the transcripts for
full-text and semantic search. An LLM pulls structured information out of each transcript,
starting with book recommendations. The built-in player is tied to the transcript: tap any line
to play from that point, and every search hit opens the player at its timestamp.

GPU work (ASR, and the LLM if it runs locally) happens on Google Colab VMs, driven through
[colabkit](https://github.com/farrellm/colabkit). The pattern is the one Degas uses: a worker
process on the VM, reached over an SSH tunnel. The difference is that Hearsay's GPU work is
**batch**, not interactive. The server starts a session when there's a backlog, drains it,
and stops the session.

### Goals

- Subscribe by RSS URL or OPML import. New episodes download and transcribe without any action
  from you.
- Word-level timestamped transcripts, with speaker labels.
- One search box over the whole library, combining keyword search (exact phrases, names) and
  semantic search ("the episode where they argued about nuclear power"). Results jump straight
  to the moment in the audio.
- LLM extraction of book recommendations (and a small set of other kinds, §6) into lists you
  can browse across all shows, each item linked back to the moment it was mentioned.
- A good player on iOS: background playback, lock-screen controls, speed control, resume
  position, and a transcript that follows playback.
- Predictable GPU spend: batched sessions, an idle shutdown, and a compute-unit budget.

### Non-goals (v1)

- Multiple users or accounts. Access control is tailnet membership, as in Degas.
- Video podcasts (the audio track only), paid/authenticated feeds beyond HTTP basic auth or a
  token in the URL, and podcast discovery/directory search.
- A native iOS app or CarPlay.
- Editing transcripts by hand. Retranscribing with a better model is the fix.
- Full offline mode. Offline playback of a few downloaded episodes is a late phase (§13).

## 2. Architecture

```
┌──────────────────┐  HTTPS (tailscale serve)  ┌──────────────────────────────┐  colab CLI (new/stop/exec)  ┌────────────────────────────┐
│ iPhone PWA       │ ────────────────────────▶ │ Hearsay server (home box)    │ ──────────────────────────▶ │ Colab VM (GPU)             │
│ React + Vite     │ ◀── SSE events, Web Push  │ FastAPI + SQLite             │  ssh (colab ssh ProxyCmd)   │ • kernel: bootstrap only   │
│ <audio> player   │ ◀── audio (HTTP Range)    │ • REST API + static UI       │  -L tunnel ⇄ worker HTTP    │ • hearsay_worker (uvicorn, │
└──────────────────┘                           │ • feed poller, downloader    │ ◀─────────────────────────▶ │   127.0.0.1:8765)          │
                                               │ • work queue + GPU scheduler │  scp audio up, JSON down    │ • ASR + alignment +        │
                                               │ • session manager (colabkit) │                             │   diarization              │
                                               │ • FTS5 + sqlite-vec index    │                             │ • embedder (batch)         │
                                               │ • query embedder (CPU)       │                             │ • LLM (vLLM), optional     │
                                               │ • LLM client (Claude API),   │                             └────────────────────────────┘
                                               │   optional                   │ ── HTTPS ──▶ podcast hosts, Open Library
                                               └──────────────────────────────┘
```

- **Frontend.** A React + Vite PWA, served by the server as static files.
- **Server.** Python 3.13 and FastAPI, with SQLite for metadata and search. Audio and
  transcripts live on the local filesystem. The server is the only component that calls the
  `colab` CLI (through colabkit's `AsyncColabCli` and `SshTunnel`). It also runs every job
  that doesn't need a GPU: feed polling, downloads, transcoding, indexing, query-time
  embeddings, and the LLM calls when the LLM is a hosted API.
- **Worker.** A Python package, `hearsay_worker`, that runs a small FastAPI/uvicorn server on
  the VM, bound to `127.0.0.1`. It is started from the Colab kernel so that it inherits the
  CUDA environment, and the server reaches it through the tunnel. Models stay loaded between
  episodes. The worker never imports `hearsay`. `hearsay_worker/spec.py` defines the types
  shared by both sides.

### Network and security

The same as Degas:

- The server binds to localhost, behind `tailscale serve` for HTTPS on a `*.ts.net`
  certificate. HTTPS is required for PWA install, Web Push and the Media Session API.
- There is no application-level auth.
- `colab` credentials, the Hugging Face token (for gated diarization models) and the
  Anthropic API key (if used) live only on the server. The HF token goes to the worker as an
  environment variable at bootstrap, never to the browser.
- The worker only listens on the VM's loopback interface.

## 3. Pipeline

Each episode moves through a fixed set of stages. Each stage is a row in `tasks` (§7.2). A
stage can run once its inputs exist, so a failure retries only that stage.

```
discovered ─▶ downloaded ─▶ prepared ─▶ transcribed ─▶ indexed ─▶ extracted
   (feed)      (server)     (server)     (GPU)         (server*)   (LLM)
```

\* Chunk embeddings are made on the GPU in the same worker call as ASR when a session is up.
The server can also make them on its CPU (§5.2).

1. **Discover** (server). The poller fetches each feed on its interval (§4) and upserts
   episodes keyed by `guid`, or by the enclosure URL when there's no guid.
2. **Download** (server). Fetch the enclosure with redirects and resume, then store it
   content-addressed (§7.1). Record the final URL, size, sha256, and duration from `ffprobe`.
   Downloads run 2 at a time.
3. **Prepare** (server, ffmpeg). Transcode to 16 kHz mono Opus at 32 kb/s for the GPU. One
   hour is about 14 MB, compared with 50–100 MB for the original MP3, which matters on a home
   uplink. The original file is kept for playback.
   - Both files must share one timeline, so timestamps on the prepared file are valid on the
     original. ffmpeg's decode handles MP3 encoder delay and Opus pre-skip. Phase 0 checks
     the drift on real MP3s and AACs: it must stay under 50 ms over 2 h.
4. **Transcribe** (GPU). Upload the prepared file and get back a transcript JSON (§5.1).
5. **Index** (server). Write segments into SQLite and FTS5. Write embedding chunks into
   sqlite-vec.
6. **Extract** (LLM). Run the extraction prompts (§6), store the items, and resolve books
   against Open Library.

**Why the server uploads its own copy instead of letting the VM fetch the enclosure:** many
podcast hosts insert ads dynamically, so two downloads of the "same" episode can differ in
length and content. The transcript must match the exact bytes you play. The VM therefore
always works on the server's file. Colab's bandwidth would make a direct fetch faster, but
the result would be wrong.

**Feed-provided transcripts.** If a feed has a Podcasting 2.0 `<podcast:transcript>`, it is
stored as metadata. Hearsay still runs its own ASR: feed transcripts usually lack word timings,
and they may describe a different ad cut. A per-feed setting, "trust feed transcript", skips
ASR for feeds whose transcripts are known to be good (an open question, §14). Feed
`<podcast:chapters>` are kept and shown in the player.

## 4. Feeds

- Add a feed by URL, or import OPML. Export OPML from settings.
- Polling uses conditional GET (`ETag`, `If-Modified-Since`). The default interval is 60 min,
  with jitter. Feeds that haven't published in 90 days are polled daily. A manual refresh is
  always available.
- Parsing uses `feedparser`, plus the Podcasting 2.0 namespace (`podcast:transcript`,
  `podcast:chapters`, `podcast:person`, `podcast:guid`).
- **Per-feed policy:**
  - `auto`: download and process every new episode (the default).
  - `manual`: list new episodes, but download and process only the ones you pick.
  - Backfill: when a feed is added, process its newest N episodes (default 10) or none. Later,
    you can "Process all" or pick older episodes. The UI shows the GPU time a backfill will
    take, using the measured real-time factor (§8.4).
- **Episode identity changes.** When a host re-publishes an episode with a new enclosure URL
  but the same guid, the episode keeps its identity. A changed sha256 is noted, but the
  episode isn't re-downloaded unless you ask. A sha256 that changes again and again is
  logged as dynamic ad insertion.
- Feed artwork and episode images are cached locally (resized to 512 px and 128 px).

## 5. GPU work (colabkit)

### 5.1 Worker and models

The worker exposes:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | GPU name, free VRAM and disk, loaded models, worker version |
| POST | `/transcribe` | `{episode_id, remote_path, language?, diarize, num_speakers?}` → job id |
| GET | `/jobs/{id}` | Status and progress (fraction of audio processed) |
| GET | `/jobs/{id}/events` | SSE progress |
| POST | `/embed` | `{texts[], model}` → vectors (batch) |
| POST | `/llm/extract` | `{transcript_text, schema, prompt}` → JSON (only when the LLM runs locally) |
| GET | `/state` | In-flight jobs and finished results not yet collected (for recovery) |

The worker has one GPU job slot. Requests queue in the worker, and the server keeps at most two
episodes in flight: one running, one uploaded and waiting. That hides upload time behind
compute.

**ASR stack (the default; Phase 0 confirms it with a benchmark):**

- **ASR:** `faster-whisper` with `large-v3-turbo` on CTranslate2 fp16, Silero VAD, and
  batched inference. It handles many languages, adds punctuation and casing well, and is
  fast: Phase 0 measures how many minutes of audio it processes per GPU-minute on T4 and L4.
- **Word timestamps:** WhisperX-style forced alignment with a wav2vec2 model per language,
  because Whisper's own word timings are loose. Tight word timings matter for seeking from a
  tapped word and for highlighting along with playback.
- **Diarization:** `pyannote/speaker-diarization-3.1` (a gated model, so it needs the HF
  token). It is on by default, but you can turn it off per feed (solo shows). Speakers come
  back as `S0`, `S1`, …. You can rename a speaker per episode, and a per-feed default name
  map ("S0 is usually the host") is applied to new episodes as a guess, not as a fact.
- **Alternative to benchmark:** NVIDIA Parakeet TDT (English only). It is much faster and has
  native word timestamps. If the library is mostly English and it wins on accuracy, it
  becomes the English default, with Whisper as the fallback for other languages.
- The ASR model, version and settings are stored with every transcript
  (`transcripts.engine`). Retranscribing with a new engine creates a new transcript version,
  and the newest successful version is the current one.

**Transcript JSON** (written by the worker, stored compressed on the server):

```json
{
  "engine": {"asr": "faster-whisper/large-v3-turbo", "align": "wav2vec2-...", "diarize": "pyannote-3.1"},
  "language": "en",
  "duration_s": 3712.4,
  "speakers": ["S0", "S1"],
  "segments": [
    {"i": 0, "start": 0.42, "end": 6.10, "speaker": "S0", "text": "Welcome back to the show.",
     "words": [{"w": "Welcome", "s": 0.42, "e": 0.80, "p": 0.98}, ...]}
  ]
}
```

A segment is roughly one sentence, split at speaker changes and pauses, and never longer than
30 s.

### 5.2 Embeddings

- **Model:** a small open embedding model that runs on the server's CPU at query time, for
  example `Qwen3-Embedding-0.6B` or `bge-small-en-v1.5`. Phase 0 picks it, using a
  hand-labelled query set from your own library. Query embedding must work when no GPU session
  is up, so the query side is always the CPU.
- **Documents:** chunks of 45–60 s, cut at segment boundaries, with a 1-segment overlap and
  the speaker names inline. Each chunk is prefixed with the show and episode title, for
  context. One hour gives about 70 chunks.
- When a session is up, the worker embeds an episode's chunks right after transcribing it, at
  almost no extra cost. Otherwise the server does it on its CPU (about 70 chunks per hour of
  audio, a few seconds of work). Both sides load the same model at the same revision. The
  model id is stored with each vector, and changing the model re-embeds everything in the
  background.

### 5.3 Session scheduling

Degas starts sessions by hand. Hearsay starts them from the backlog.

- **Start** when GPU work is pending and either:
  - the backlog reaches `min_batch_minutes` of audio (default 120), or
  - the oldest pending item has waited `max_wait_hours` (default 6), or
  - you tap **Process now** (from an episode or from the session sheet).

  Before starting, check the budget (below).
- **GPU choice:** `T4` by default (the cheapest, and enough for the ASR stack). If the LLM
  runs locally, use `L4`, or whatever the LLM's minimum is (§6.3). Fall back down the
  `GPUS` list when `colab new` raises `ColabNoGpu`.
- **Bootstrap** (as in Degas §3.1):
  1. `colab new -s hearsay --gpu …`.
  2. Open the tunnel (`SshTunnel(acli.proxy_command(...), key, remote_port=8765)`).
  3. Upload the worker bundle if its content hash has changed.
  4. `pip install` the worker's requirements (cached on the VM for its lifetime).
  5. `colab exec` a bootstrap that `Popen`s uvicorn.
  6. Poll `/health`.

  Model weights download from Hugging Face on the VM itself. Colab's bandwidth makes that
  faster than copying them from the home box.
- **Drain:** feed the worker episodes in priority order (below), two in flight.
- **Stop** when the queue is empty and stays empty for `idle_grace_minutes` (default 5, so a
  feed poll that lands mid-drain doesn't cause a restart), or when the budget runs out. Stop
  with `cli.stop(session)` and confirm with `status`.
- **Priority:** Process now > episodes in your Up Next > new episodes from `auto` feeds,
  newest first > backfill, newest first.
- **Budget:** `colab usage` gives the account's balance and burn rate, as in Degas. The rate
  is read before and after `new` to get this session's rate. A monthly compute-unit cap
  (`budget_units_per_month`) and a reserve floor (`min_balance`, so Hearsay never uses the
  last units Degas might want) are both checked before starting, and every 5 min while
  running. When the cap is hit, automatic starts pause until the next month. Process now
  still works, after a confirmation.
- **Sharing the account with Degas:** colabkit reports every VM on the account. Hearsay only
  ever touches its own session name (`hearsay`). If the account's assignment count says
  another VM is up, Hearsay still starts, since Colab allows several, but the session sheet
  shows it.
- **Recovery:** if the server restarts with a session recorded as live, it checks
  `colab status`, re-opens the tunnel and calls `GET /state` to collect finished results.
  Anything in flight that's unaccounted for goes back to the queue. If the VM is gone, the
  session is marked ended and its tasks are re-queued. Every task is idempotent: it is keyed
  by episode and stage, and a transcribe result for a superseded file is discarded.
- **Liveness:** the same open question as in Degas: whether a VM whose kernel is idle and
  only the SSH-launched worker is busy gets reclaimed. Whatever answer Degas settles on, use
  it. If it's needed, send a trivial `colab exec` every few minutes during a drain.

## 6. LLM extraction

### 6.1 What gets extracted

Each kind has a JSON schema and a prompt. Every item carries the timestamp and a short quote
of evidence, so it links back to the audio. v1 kinds:

| Kind | Fields | Notes |
| --- | --- | --- |
| `book` | title, author?, recommended_by (speaker or name), sentiment (`recommends` / `mentions` / `criticizes`), quote, t | The headline feature |
| `media` | title, type (film, show, podcast, article, paper), creator?, quote, t | |
| `person` | name, role (guest, host, mentioned), quote, t | Guests are also read from `podcast:person` |
| `link` | url or product name, quote, t | Things like "go to example.com/show" |
| `ad_segment` | start, end, sponsor | Shown as skippable bands in the player (with a skip button, never an automatic skip) |
| `summary` | 3–5 sentences, plus chapter titles with start times when the feed has no chapters | Shown on the episode page |

Kinds are entries in a registry (`extract/kinds/<kind>.py`: a schema, a prompt, and a
normalizer). Adding one later means adding a module and running a backfill for that kind
alone.

### 6.2 How it runs

- **Input:** the transcript as `[mm:ss] Speaker: text` lines. One hour is about 12–15k
  tokens, which fits in one call for both provider options, so v1 does no chunking. Episodes
  longer than the context budget are split into overlapping windows, and items are
  de-duplicated across windows by (kind, normalized title, t within 60 s).
- **Output:** structured JSON that is validated against the schema. One retry on a validation
  failure. Items with a timestamp outside the episode, or a quote that doesn't
  fuzzy-match the transcript near that timestamp, are dropped. This is the main guard against
  hallucinated items.
- **Book resolution:** query the Open Library search API by title and author. Accept the top
  hit when it matches above a threshold, storing the work id, canonical title, author and
  cover. Otherwise keep it unresolved, shown with a "?" that you can fix by picking from the
  candidates. The same book mentioned in many episodes rolls up to one row in the Books view.
- Extraction results are versioned by (prompt version, model). Changing a prompt can re-run
  that kind over the library as a background job.

### 6.3 Provider (an open decision, §14)

The extractor sits behind one interface: `extract(transcript, kind) -> items`. There are two
providers.

- **Local, on the Colab GPU (default when the GPU is up):** vLLM, serving an open-weight
  instruct model with JSON-schema guided decoding, in the same worker. A mid-size model
  quantized to 4 bits fits on an L4 with room for a 16k context. It costs nothing per token,
  but it raises the GPU tier from T4 to L4 and adds model load time (1–3 min per session). It
  runs in the same drain: transcribe, then extract while the next upload is in flight.
- **Hosted (Claude API):** called from the server, so it needs no GPU and can run at any time,
  including re-runs. It costs per token, but extraction quality is likely better, and a
  re-run doesn't need a session. A small, fast model (Haiku class) is probably enough for
  this kind of structured extraction. The model id is a setting.

Recommendation: build the provider interface in Phase 4, try both on 20 hand-labelled
episodes (precision and recall of books), and pick per kind. If the local model is close
enough, it stays the default, which keeps everything GPU-related inside colabkit as
requested. If it isn't, use the hosted model for extraction and keep the GPU sessions on T4
for ASR only.

## 7. Data model and storage

### 7.1 Filesystem

```
data/
  hearsay.db                       SQLite (WAL): metadata, FTS5, sqlite-vec
  audio/ab/abcdef….mp3             originals, content-addressed by sha256
  prepared/ab/abcdef….opus         16 kHz mono, for the GPU; deleted once transcribed
  transcripts/<episode_id>/<ver>.json.zst
  images/<sha256>-{512,128}.webp
```

### 7.2 Tables

- **`feeds`**: id, url, title, author, image, etag, last_modified, last_polled_at,
  poll_interval, policy (`auto`/`manual`), backfill_n, diarize, speaker_map (JSON),
  trust_feed_transcript, error, created_at.
- **`episodes`**: id, feed_id, guid, title, description (sanitized HTML), published_at,
  enclosure_url, enclosure_type, duration_s, audio_sha256, audio_path, chapters (JSON),
  feed_transcript_url, image, state (the furthest finished stage), error.
- **`tasks`**: id, episode_id, stage (`download`, `prepare`, `transcribe`, `index`,
  `extract:<kind>`), status (`pending`, `running`, `done`, `error`), priority, attempts,
  last_error, session_id?, created_at, updated_at. Unique on (episode_id, stage) for
  incomplete tasks.
- **`transcripts`**: id, episode_id, version, engine (JSON), language, path, word_count,
  created_at, is_current.
- **`segments`**: transcript_id, i, start, end, speaker, text. Words stay in the JSON file:
  about 10k words per hour would make per-word rows the biggest table by far, for no query
  that needs them.
- **`segments_fts`**: an FTS5 table over `segments.text` (external content). Tokenizer
  `porter unicode61 remove_diacritics 2`.
- **`chunks`**: id, transcript_id, start, end, text. **`chunks_vec`**: a sqlite-vec `vec0`
  table holding (chunk_id, embedding), along with the model id.
- **`speakers`**: transcript_id, label (`S0`), name (yours or guessed), guessed (bool).
- **`items`**: id, episode_id, kind, data (JSON), t, quote, entity_id?, prompt_version,
  model, hidden (bool, for "this is wrong").
- **`entities`**: id, kind (`book`, …), canonical (JSON: title, author, Open Library id,
  cover), plus a mention count (a view).
- **`playback`**: episode_id, position_s, speed, finished, updated_at. **`queue`** (Up Next):
  position, episode_id.
- **`sessions`**: id, gpu, started_at, ready_at, ended_at, end_reason, unit_rate,
  audio_minutes_processed. **`settings`**: key, value.

### 7.3 Retention

- Originals: keep unplayed episodes. Delete played episodes' audio after `audio_keep_days`
  (default 30), or never. Transcripts, items and the index are kept forever, since they're
  small (about 100 KB per hour compressed).
- An episode whose audio was deleted can still be played by streaming its enclosure URL.
  Because of dynamic ad insertion, the timestamps may then be off. The player compares the
  stream's duration with the stored duration, and when they differ by more than 2 s it shows a
  "timestamps may be offset" note. A "Re-download" action restores an exact copy, if the host
  still serves the same bytes. If you care about this for a show, set its retention to forever.

## 8. Search

### 8.1 Query flow

One box. Every query runs two retrievals and fuses the results:

1. **Keyword:** FTS5 `MATCH` over segments, using bm25. It supports `"exact phrases"`,
   prefix `foo*`, and `-exclude`. A query is passed through as FTS5 syntax when it parses as
   that, and is otherwise quoted term by term.
2. **Semantic:** embed the query on the CPU and take the top-k chunks by cosine similarity in
   sqlite-vec.
3. **Fusion:** reciprocal rank fusion (k = 60). Each fused hit points at an episode and a time
   range. A segment hit inside a chunk hit merges into one result.
4. **Grouping:** results are grouped by episode (best hit first), with up to 3 moments per
   episode. Each moment shows a highlighted snippet (FTS5 `snippet()` for keyword hits, and
   the best-matching segment for semantic hits), the speaker, and a timestamp.

Filters: show, date range, speaker name, and "has items of kind X". A **mode toggle**
(Both / Exact / Meaning) is there for when fusion gets in the way.

### 8.2 Other entry points

- **Books, Media, People** tabs browse `entities` and `items` across the library. Sort by
  mention count or by most recent. Each mention links to its moment.
- **Within an episode:** a find-in-transcript bar on the player screen, which runs on the
  client over the loaded transcript.

### 8.3 Performance targets

For a library of 1,000 hours (about 1M segments and 70k chunks): keyword under 50 ms, CPU
query embedding under 150 ms, vector search under 50 ms. Brute-force `vec0` is fine at this
size. Revisit quantized or ANN indexes past about 1M chunks.

### 8.4 Real-time factor

The minutes of audio processed per GPU-minute are measured for each (GPU, engine) from
`sessions` and `tasks`. They feed the backfill estimates, the "this will take about 40 min
of T4 time" prompts, and the compute-unit estimates.

## 9. Player

### 9.1 Behavior

- An HTML `<audio>` element. The server serves audio with HTTP Range support (iOS requires
  it) and the correct `Content-Type`.
- **Transcript view:** a virtualized list of segments with speaker labels. The current
  segment is highlighted, and the current word too when the episode has word timings. It
  auto-scrolls; scrolling by hand pauses auto-scroll and shows a "Back to now" pill.
- **Seek from the transcript:** tapping a segment plays from its start. Long-pressing a word
  plays from that word. A segment's action sheet offers *Copy text*, *Copy link to this
  moment* and *Share*.
- **Deep links:** `/e/<episode_id>?t=1234.5` opens the episode at that time. Search results
  and items use these links.
- **Controls:** play/pause, back 15 s and forward 30 s (configurable), speed 0.8–3× per
  episode (remembered per feed as a default), a sleep timer, chapter and ad bands on the
  scrubber, and Skip ad when inside an `ad_segment`.
- **Resume:** the position is saved every 10 s and on pause/`pagehide`
  (`navigator.sendBeacon`). Episodes are marked finished at 95% or when they end.
- **Up Next:** a manual queue, with auto-advance.
- **A persistent mini-player** sits above the tab bar on every screen.

### 9.2 iOS specifics

- **Background audio and lock screen:** set the Media Session API metadata (title, show,
  artwork) and action handlers (play, pause, seekbackward, seekforward, seekto,
  previoustrack, nexttrack) on every track change. Keep one long-lived `<audio>` element and
  swap its `src`, rather than creating new elements. iOS ties the audio session to the
  element.
- Playback in a standalone home-screen PWA continues with the screen locked and the app in
  the background. Web Push and installation need iOS 16.4+. Phase 0 tests background playback
  across a 2 h episode and app switching, since this has regressed in past iOS releases (§14).
- **`visibilitychange`:** on return to the app, refetch the state and re-sync the transcript
  view to `currentTime`. SSE reconnects automatically.
- **Audio session interruptions** (calls, Siri): listen for `pause` without a user action,
  and don't auto-resume.
- **Web Push notifications:** "N new episodes transcribed" (batched per drain), budget
  warnings, feed errors. Off by default per feed.

## 10. Server API

| Method | Path | Purpose |
| --- | --- | --- |
| GET/POST | `/api/feeds` | List; add `{url}` |
| POST | `/api/feeds/import-opml` | Upload OPML; `GET /api/feeds/export-opml` |
| GET/PATCH/DELETE | `/api/feeds/{id}` | Detail and episodes; edit the policy; unsubscribe (keeps or purges data) |
| POST | `/api/feeds/{id}/refresh` | Poll now |
| GET | `/api/episodes` | Filtered list: feed, state, unplayed, has-transcript |
| GET | `/api/episodes/{id}` | Metadata, playback, stage states, speakers |
| POST | `/api/episodes/{id}/process` | Queue (with `now: true` = Process now) |
| POST | `/api/episodes/{id}/retranscribe` | New transcript version |
| GET | `/api/episodes/{id}/transcript` | The current transcript JSON (gzip) |
| PATCH | `/api/episodes/{id}/speakers` | Rename speakers |
| GET | `/api/episodes/{id}/items` | Extracted items |
| GET | `/api/audio/{id}` | Audio with Range |
| PUT | `/api/playback/{id}` | Position, speed, finished |
| GET/PUT | `/api/queue` | Up Next |
| GET | `/api/search?q=&mode=&feed=&from=&to=&speaker=` | Grouped results (§8) |
| GET | `/api/entities?kind=book` | Books and similar lists; `GET /api/entities/{id}` lists mentions |
| PATCH | `/api/items/{id}` | Hide, or re-link to another entity |
| GET/POST/DELETE | `/api/session` | GPU session state, budget, backlog estimate; start (Process now); stop |
| GET | `/api/events` | SSE: task progress, session state, new episodes |
| GET/PUT | `/api/settings` | |
| POST | `/api/push/subscribe` | Web Push |

## 11. Frontend

- **Stack:** React 19, Vite, TypeScript, TanStack Query and `vite-plugin-pwa`, matching
  Degas, so conventions, components and the deploy recipe carry over. Transcripts use a
  virtualized list (`@tanstack/react-virtual`).
- **Tabs:** **Library** (shows, then episodes), **Search**, **Books** (with Media and People
  as segments), and **Up Next**. The header has a GPU chip: idle, backlog N h, or draining
  with progress. It opens the Session sheet.
- **Episode screen:** artwork, title and metadata. Stage pills (Downloaded, Transcribed,
  Indexed, Extracted) showing progress or an error with Retry. A summary, chapters, the items
  extracted from this episode, then the transcript. Playing turns this screen into the
  player.
- **Session sheet:** state, GPU, current episode and progress, backlog and its time and
  compute-unit estimate, the month's spend against the budget, Process now / Stop, and
  settings for thresholds and the GPU.
- **Feed settings:** policy, backfill, diarization on/off, speaker name defaults, retention,
  and default speed.

## 12. Deployment

- `hearsay serve` runs a single process on `127.0.0.1:<port>`. `tailscale serve
  --bg --https=<port>` exposes it, using the next free HTTPS port after Degas's 8448
  (8449).
- A systemd user unit `deploy/hearsay.service`, with the same pattern as Degas:
  `Restart=always`, `tailscale serve` in `ExecStartPost`, and linger enabled.
- Config lives in `hearsay.toml`: data dir, `colab` binary and auth mode, SSH key path, HF
  token, Anthropic API key (optional), VAPID keys, budget, and scheduler thresholds.
- Host dependencies: `ffmpeg`/`ffprobe`, OpenSSH, the `colab` CLI (`uv tool install
  google-colab-cli`), and the embedding model weights (downloaded on first run, about
  0.5–1.2 GB).
- colabkit is a pinned tarball dependency, as in Degas. Changes to the CLI wrapper or the
  tunnel go to colabkit, then the tag is bumped.

### Repository layout

```
hearsay/
  docs/design.md
  server/src/hearsay/   app.py, api/ (routers, schemas, errors), db/ (schema, queries, rows),
                        feeds/ (poller, parser, opml), media/ (download, ffmpeg prepare,
                        images), gpu/ (scheduler, session mgr, worker client, bundle),
                        search/ (fts, vectors, fusion, embedder), extract/ (provider iface,
                        kinds/, openlibrary), push.py, services.py
  worker/src/hearsay_worker/  app.py, jobs.py, asr.py, align.py, diarize.py, embed.py,
                              llm.py (vLLM, optional), spec.py (shared with server)
  worker/requirements-worker.txt
  web/                  Vite React app
  hearsay.toml.example
```

Tests fake the colab CLI and tunnel, as Degas does, and run the real worker app over ASGI with
stub models, so CI needs no GPU or model weights.

## 13. Implementation phases

0. **Spikes** (a throwaway `spike/` directory):
   - ASR benchmark on 5 of your real episodes (one with music, one with crosstalk, one in
     another language): faster-whisper turbo + alignment + pyannote, against Parakeet, on T4
     and L4. Measure word error rate (spot-checked), real-time factor, word-timing accuracy
     and VRAM.
   - Timeline drift between the original and the prepared Opus file.
   - Background playback in an iOS standalone PWA (2 h, locked, app switching).
   - Embedding model choice from 30 labelled queries.
   - Whether sessions survive while only the worker is busy (reuse the Degas finding).
1. **Feeds and the player:** subscribe, OPML, download, Range serving, the player with Media
   Session, resume and Up Next. This is useful as a plain podcast app before any GPU work.
2. **Transcription:** worker, bundle, session manager, scheduler with thresholds and budget,
   prepared audio, transcript storage, and the transcript view with tap to seek and
   follow-along.
3. **Search:** FTS5, embeddings (worker and CPU), fusion, the grouped results UI, and deep
   links.
4. **Extraction:** the provider interface, the books kind, Open Library resolution, the Books
   tab, and evaluation of local against hosted (§6.3).
5. **More kinds:** media, people, links, ad segments (with scrubber bands and skip), and
   summaries and chapters.
6. **Polish:** speaker naming and per-feed maps, Web Push, retention, re-transcription with a
   new engine, and a backfill planner.
7. **Offline (optional):** "Download to device" for a few episodes. The service worker caches
   the audio and serves Range requests from the Cache API, and the transcript is cached too.
   iOS storage quotas and eviction make this best-effort.

## 14. Risks and open questions

- **LLM provider** (§6.3): local vLLM keeps everything on colabkit, but needs L4 sessions
  and gives weaker extraction. The Claude API is better and needs no GPU, but costs per
  token. Decide from the Phase 4 evaluation.
- **iOS PWA background audio** has broken in some iOS releases, and Hearsay can't work around
  it. If it's unreliable, the fallback is "Open in Podcasts/Overcast at this time" for
  long listening, using Hearsay for search and reading.
- **Dynamic ad insertion** makes the timestamps of streamed (not stored) audio unreliable.
  Mitigation: keep originals (§7.3).
- **Diarization quality** on crosstalk-heavy shows. Speaker labels are shown as best effort,
  and per-feed diarization can be turned off.
- **Colab supply and reclaim:** a drain may be cut off mid-episode. Tasks are idempotent and
  re-queue, so the cost is lost GPU time, not lost data.
- **Feed transcripts:** should a good publisher transcript (with timings, such as SRT or
  VTT) replace ASR to save GPU time? The default is no. Revisit once the real-time factor is
  known, since ASR may cost so little that it isn't worth the complexity.
- **Copyright and hosting:** everything stays on your own tailnet for personal use.
  Hearsay never republishes audio or transcripts.

## 15. Future work

- "Ask the library": RAG over chunks, with answers that cite timestamps. The retrieval half
  is already §8.
- Cross-episode entity pages for people ("every episode where X is a guest").
- Clip export: a time range as an audio snippet plus its transcript text, for sharing.
- Smart Up Next from your listening history plus extracted topics.

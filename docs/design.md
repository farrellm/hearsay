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
                                               │ • FTS5 + sqlite-vec index    │                             │ • embedder (later)         │
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
   (feed)      (server)     (server)     (GPU)         (server)    (LLM)
```

1. **Discover** (server). The poller fetches each feed on its interval (§4) and upserts
   episodes keyed by `guid`, or by the enclosure URL when there's no guid.
2. **Download** (server). Fetch the enclosure with redirects and resume, then store it
   content-addressed (§7.1). Record the final URL, size, sha256, and duration from `ffprobe`.
   Downloads run 2 at a time.
3. **Prepare** (server, ffmpeg). Transcode to 16 kHz mono Opus at 32 kb/s for the GPU. One
   hour is about 14 MB, compared with 50–100 MB for the original MP3, which matters on a home
   uplink. The original file is kept for playback.
   - Both files must share one timeline, so timestamps on the prepared file are valid on the
     original. ffmpeg's decode handles MP3 encoder delay and Opus pre-skip. Phase 0 measured
     the offset on 6 MP3s and 3 AACs (up to 3.7 h) at under 0.1 ms, with no drift.
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
| POST | `/embed` | `{texts[], model}` → vectors (batch). Not in v1 (§5.2) |
| POST | `/llm/extract` | `{transcript_text, schema, prompt}` → JSON (only when the LLM runs locally) |
| GET | `/state` | In-flight jobs and finished results not yet collected (for recovery) |

The worker has one GPU job slot. Requests queue in the worker, and the server keeps at most two
episodes in flight: one running, one uploaded and waiting. That hides upload time behind
compute.

**ASR stack (benchmarked in Phase 0, [spikes.md](spikes.md) §M0.1):**

- **ASR:** `faster-whisper` with `large-v3-turbo` on CTranslate2 fp16, Silero VAD, and
  batched inference (batch 16). It handles many languages and adds punctuation and casing
  well. It runs at 52× real time on a T4 and 90× on an L4. The language hint is the feed's
  `<language>` (or a per-feed override). Auto-detection runs only when there's none: in
  Phase 0 it mislabelled a Spanish show whose episode opens with English ads.
- **Segments:** the batched pipeline returns ~30 s VAD chunks, so the worker rebuilds
  segments from the word timings. It splits at sentence ends, speaker changes and pauses of
  1 s or more, with a 30 s cap. It also drops known Whisper hallucinations ("Thanks for
  watching", "¡Suscríbete al canal!") on segments that are mostly non-speech.
- **Word timestamps:** forced alignment with torchaudio's multilingual `MMS_FA` wav2vec2
  model, a single model for all languages. Whisper's own word timings run about 90 ms early,
  and they're loose. Tight word timings matter for seeking from a tapped word and for
  highlighting along with playback.
- **Diarization:** still open (spikes.md §M0.1.6). The candidates are
  `pyannote/speaker-diarization-community-1` (pyannote 4.x; 3.x no longer installs on the
  Colab image, and the model is gated, so it needs the HF token) and NVIDIA's ungated
  `diar_streaming_sortformer_4spk-v2` (at most 4 speakers, and it pulls in NeMo). It is on by
  default, but you can turn it off per feed (solo shows). Speakers come
  back as `S0`, `S1`, …. You can rename a speaker per episode, and a per-feed default name
  map ("S0 is usually the host") is applied to new episodes as a guess, not as a fact.
  Re-transcribing relabels everyone, so names carry over by time overlap: each new speaker
  takes the name of the old speaker they share the most speaking time with, if that's over
  half of the new speaker's time. Carried-over names keep their guessed or confirmed state.
  Speakers with no match fall back to the per-feed map.
- **Rejected: NVIDIA Parakeet TDT 0.6B v3.** It is only slightly faster (66× vs. 52× on a
  T4), and it silently dropped whole minutes of speech near music, and in Spanish.
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

- **Model:** `BAAI/bge-small-en-v1.5` at revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`
  (384 dimensions), chosen in Phase 0 (spikes.md §M0.5). It takes 16 ms per query and about
  10 s of CPU per audio hour for documents on the home box. Qwen3-Embedding-0.6B was far too
  slow on that CPU. Query embedding must work when no GPU session is up, so the query side is
  always the CPU. Cross-lingual queries (English questions about a Spanish show) are its weak
  spot. Revisit if the library gets much non-English audio.
- **Documents:** chunks of 45–60 s, cut at segment boundaries, with a 1-segment overlap and
  the speaker names inline. Each chunk is prefixed with the show and episode title, for
  context. One hour gives about 70 chunks.
- In v1 the server embeds every chunk on its CPU, in the index stage. That's about 70 chunks
  per hour of audio, about 10 s of work, and it means only one copy of the model has to be
  kept at a pinned revision. The model id is stored with each vector, and changing the model
  re-embeds everything in the background.
- Later, if a model change makes re-embedding the whole library on the CPU too slow, the
  worker's `/embed` can do the bulk re-embed during a drain. It must load the same model at
  the same revision.

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
- **Session lifetime:** Colab VMs have a maximum lifetime. Phase 0 didn't measure it, so
  `max_session_hours` defaults to 10. Each session's age at an unexpected reclaim is logged,
  so the real limit shows up from use. Once a session passes that age,
  it stops handing out new episodes, lets the ones in flight finish, stops, and starts a fresh
  session if the backlog still meets the start rule. A long backfill becomes several
  sessions instead of one that gets cut off.
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
  by episode and stage, and records the input it was run on (`tasks.input_ref`). A result
  whose input no longer matches the episode (a re-downloaded file, or a newer transcript
  version) is discarded.
- **Liveness:** in Phase 0, a VM whose kernel sat idle for 45 min, while only the
  SSH-launched worker was busy, was not reclaimed. So there's no `exec` heartbeat by default.
  It stays available as a setting (`keepalive_exec_minutes`, off). Unexpected `SessionGone`s
  are logged with the kernel's idle time.
- **Never do long work inside `colab exec`.** An `exec` that prints nothing for about a minute
  can return early and leave its work unfinished. `exec` only `Popen`s the detached worker.
  Its `--env` values persist in the kernel, so per-job settings never go through `--env`.

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
  last_error, input_ref, session_id?, created_at, updated_at. `input_ref` names the exact
  input: the audio sha256 for `prepare` and `transcribe`, and the transcript id for `index`
  and `extract:*`. Unique on (episode_id, stage) for incomplete tasks.
- **`transcripts`**: id, episode_id, version, engine (JSON), language, path, word_count,
  created_at, is_current.
- **`segments`**: transcript_id, i, start, end, speaker, text. Words stay in the JSON file:
  about 10k words per hour would make per-word rows the biggest table by far, for no query
  that needs them.
- **`segments_fts`**: an FTS5 table over `segments.text` (external content). Tokenizer
  `porter unicode61 remove_diacritics 2`. Only the current transcript version's segments are
  in the index. Switching `is_current` deletes the old version's rows from `segments_fts` and
  `chunks_vec` and inserts the new ones, in one transaction. Older versions keep their
  `segments` rows, but are never searched.
- **`chunks`**: id, transcript_id, start, end, text. **`chunks_vec`**: a sqlite-vec `vec0`
  table holding (chunk_id, embedding), along with the model id.
- **`speakers`**: transcript_id, label (`S0`), name (yours or guessed), guessed (bool).
  Labels are not stable across transcript versions (§5.1).
- **`items`**: id, episode_id, transcript_id, kind, data (JSON), t, quote, entity_id?,
  prompt_version, model, hidden (bool, for "this is wrong"). `t` is only valid on its own
  transcript. Queries show only items from the current transcript. A new transcript version
  queues re-extraction, and `hidden` and manual entity links carry over to the new items that
  match (kind, entity or normalized title, t within 60 s).
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

The visual design, screen layouts and interaction details are in [ui.md](ui.md).

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

- `hearsay serve` runs a single process on `127.0.0.1:8452`. `tailscale serve
  --bg --https=8452` exposes it. 8449 went to AisleFlow, and the reservation is recorded in
  `~/workspace/TAILSCALE_PORTS.md`.
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

## 13. Implementation phases and milestones

Each phase ends with something usable on the phone. Each milestone is one mergeable slice of
work, with a concrete check under "Done when". Milestones within a phase are done in order.
Phases 5–7 can be reordered once Phase 4 is done.

### Phase 0: Spikes

Throwaway code in `spike/`. Nothing in it is imported later. The output is
`docs/spikes.md`: the numbers, and the decisions they lead to.

- **M0.1 ASR benchmark.** 5 of your real episodes (one with music, one with crosstalk, one in
  another language). faster-whisper turbo + alignment + pyannote, against Parakeet, on T4 and
  L4. Measure word error rate (spot-checked), real-time factor, word-timing error (against
  ~20 hand-marked words per episode), peak VRAM and model load time.
  *Done when:* the default engine per language and the default GPU are chosen, and real-time
  factors are recorded for the §8.4 estimates.
- **M0.2 Colab round trip.** A script using colabkit: `new` → tunnel → `Popen` a trivial
  uvicorn → call it → `stop`. Run a long GPU job while the kernel sits idle, to answer the
  liveness question (§5.3). Record time-to-ready and the maximum session lifetime.
  *Done when:* the scripted round trip works unattended, and the keepalive decision is
  written down.
- **M0.3 Timeline drift.** Compare word times on the original against the prepared Opus file.
  *Done when:* drift stays under 50 ms over 2 h on at least 3 MP3s and 2 AACs, or a
  correction is chosen.
- **M0.4 iOS background playback.** A minimal PWA behind `tailscale serve`, with one
  `<audio>` element and Media Session.
  *Done when:* 2 h of locked playback, plus app switching, works on the current iOS, or the
  §14 fallback is adopted.
- **M0.5 Embedding model.** 30 labelled queries over the M0.1 transcripts plus a few more
  episodes. Compare recall@10 and CPU latency for 2–3 candidate models.
  *Done when:* a model and revision are pinned, and query embedding takes under 150 ms on the
  home box.

### Phase 1: Feeds and the player

A plain podcast app, with no GPU work.

- **M1.1 Skeleton.** The repository layout (§12), `hearsay serve`, `hearsay.toml` loading,
  the SQLite schema with migrations, the Vite app served as static files, CI (pytest, ruff,
  tsc, vitest), and the systemd unit with `tailscale serve`.
  *Done when:* the empty PWA installs on the iPhone from `https://<host>.ts.net:8452`.
- **M1.2 Feeds.** Add by URL, OPML import and export, the poller (conditional GET, jitter,
  slow polling for dormant feeds), Podcasting 2.0 fields, the image cache, and per-feed policy
  and backfill N. The Library tab.
  *Done when:* your own OPML imports and polls for a day with no unexplained errors.
- **M1.3 Downloads.** The `tasks` table and runner (download stage only), 2 concurrent
  downloads, resume, content-addressed storage, `ffprobe`, and guid/sha256 change handling.
  *Done when:* killing the server mid-download and restarting it finishes the file with the
  right sha256, and no task runs twice.
- **M1.4 Player.** The Range audio endpoint, one long-lived `<audio>` element, the
  mini-player, Media Session, skip, speed, sleep timer, resume (`sendBeacon`), feed chapters,
  and Up Next with auto-advance.
  *Done when:* you've used Hearsay as your podcast app for a week, and the M0.4 checks pass
  on the real app.

### Phase 2: Transcription

- **M2.1 Worker.** `hearsay_worker` with `spec.py`, `/health`, `/transcribe`, `/jobs`,
  `/state`, and one job slot. ASR, alignment and diarization sit behind interfaces with stub
  models for tests. The output is the §5.1 transcript JSON.
  *Done when:* ASGI tests pass with stubs, and a real episode produces a valid transcript on a
  hand-started Colab VM.
- **M2.2 One episode, end to end.** The prepare stage (ffmpeg), the session manager (bundle
  hash and upload, `pip install`, bootstrap, health poll, stop), the worker client (scp up,
  JSON down, two in flight), transcript storage and the `segments` table, and Process now on
  the episode screen.
  *Done when:* tapping Process now starts a VM, transcribes the episode, stores the
  transcript and stops the VM, with no manual steps.
- **M2.3 Transcript view.** The virtualized list, segment and word highlighting, tap and
  long-press to seek, Back to now, deep links (`/e/<id>?t=`), and find-in-transcript.
  *Done when:* the word highlight visibly tracks the speech at 1× and 2×, and a deep link
  opens at the right moment from a cold start.
- **M2.4 Automatic scheduling.** Backlog thresholds, priority order, idle grace, session
  rotation at `max_session_hours`, GPU fallback, budget and reserve checks (before `new` and every 5 min), the measured
  real-time factor, SSE events, the GPU chip and the Session sheet.
  *Done when:* for a week, new episodes from `auto` feeds are transcribed with no action from
  you, spend stays under the cap, and no session outlives its idle grace.
- **M2.5 Recovery.** Restart with a live session (`colab status`, re-tunnel, `GET /state`),
  re-queueing when the VM is gone, idempotent results, retries with backoff, and stage errors
  with Retry in the UI.
  *Done when:* each fault (server killed mid-drain, VM stopped mid-episode, tunnel dropped)
  ends with every episode transcribed exactly once. This is tested with fakes in CI, and once
  for real.

### Phase 3: Search

- **M3.1 Keyword search.** FTS5 over the current transcript version's segments, query
  handling (FTS5 syntax passed through, otherwise quoted), snippets, grouping by episode, and
  the Search tab, with each result opening the player at its timestamp.
  *Done when:* exact-phrase queries find known moments, with p95 under 50 ms on a synthetic
  1,000 h library.
- **M3.2 Semantic search.** The chunker, the CPU embedder, sqlite-vec, embedding of existing
  transcripts in the background, and re-embedding when the model changes. All of it runs on
  the CPU (§5.2).
  *Done when:* the M0.5 query set reaches the recall measured in the spike.
- **M3.3 Fusion and filters.** Reciprocal rank fusion, merging segment hits into chunk hits,
  the mode toggle, and the show, date and speaker filters.
  *Done when:* on the labelled set, Both has recall@10 at least as good as Exact or Meaning
  alone.

### Phase 4: Extraction

- **M4.1 Framework.** The provider interface, the kinds registry, prompt and model
  versioning, schema validation with one retry, the timestamp and quote guard, the
  `extract:<kind>` stage, and the hosted provider. The `book` kind first.
  *Done when:* books are extracted from every new transcript and shown on the episode screen,
  each linked to its moment.
- **M4.2 Books.** Open Library resolution, the `entities` roll-up, the Books tab, and the hide,
  re-link and "?" candidate picker actions.
  *Done when:* across the library, most book mentions resolve, and the same book mentioned in
  different episodes shows as one row.
- **M4.3 Provider evaluation.** 20 hand-labelled episodes, and the local vLLM provider in the
  worker on L4. Measure book precision and recall for both providers.
  *Done when:* a provider is chosen per kind, and §6.3 and §14 record the decision.

### Phase 5: More kinds

- **M5.1** `media`, `person` (merged with `podcast:person`) and `link`, plus the Media and
  People segments of the Books tab.
- **M5.2** `ad_segment`, with scrubber bands and Skip ad, plus `summary` and generated
  chapters for feeds without chapters.

*Done when:* each kind can be backfilled over the whole library as a background job, on its
own.

### Phase 6: Polish

- **M6.1 Speakers.** Renaming per episode, per-feed default maps applied as guesses, and the
  speaker filter using names.
- **M6.2 Web Push.** VAPID keys, subscription, batched "N new episodes" notifications, budget
  warnings, and feed errors.
- **M6.3 Retention.** `audio_keep_days`, streaming fallback with the offset note, and
  Re-download.
- **M6.4 Re-transcription and backfill planning.** New transcript versions with an
  `is_current` switch, swapping the index, re-extraction with hidden flags and entity links
  carried over, carrying speaker names over, and a backfill planner with GPU-time and
  compute-unit estimates.

### Phase 7: Offline (optional)

- **M7.1** "Download to device" for a few episodes. The service worker caches the audio and
  serves Range requests from the Cache API, and the transcript is cached too. iOS storage
  quotas and eviction make this best-effort.
  *Done when:* a downloaded episode plays in airplane mode with its transcript and seeking.

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
  re-queue, so the cost is lost GPU time, not lost data. Planned rotation at
  `max_session_hours` (§5.3) keeps long backfills from running into the lifetime limit.
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

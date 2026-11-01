# Hearsay — Phase 0 spike results

Date: 2026-11-01. Scripts: `spike/` (throwaway, not imported later). Raw results:
`spike/data/` (git-ignored; re-create with `spike/fetch.py` and the scripts below).

Status by milestone:

| Milestone | Status |
| --- | --- |
| M0.1 ASR benchmark | **Done**: Whisper turbo + MMS_FA, pyannote `community-1` diarization, T4. The hand-marked word check is still open (§M0.1.4). |
| M0.2 Colab round trip | **Done.** The keepalive decision is made. The maximum session lifetime was not measured (your call): `max_session_hours = 10` |
| M0.3 Timeline drift | **Done.** No drift. |
| M0.4 iOS background playback | **Built, waiting on you.** It needs a 2 h test on the phone (§M0.4) |
| M0.5 Embedding model | **Done.** `bge-small-en-v1.5`, pinned; 16 ms per query on the home box |

Spend: 2.7 compute units in all (T4 about 1.5 h, L4 about 25 min, CPU runtime about 50 min).

## Test episodes

These are public shows, standing in for your library. They are listed in
`spike/episodes.toml`.

| id | Show / episode | Format | Length | Used for |
| --- | --- | --- | --- | --- |
| music | Song Exploder: DJ Shadow, "Mutual Slump" | MP3 192k | 21 min | ASR: speech over music, a full song, ads |
| crosstalk | My Brother, My Brother and Me #837 | MP3 128k | 52 min | ASR: 3 similar voices talking over each other |
| spanish | Radio Ambulante: "Partida de nacimiento" | MP3 128k | 54 min | ASR: Spanish with English ads, many speakers |
| interview | EconTalk: Angela Duckworth | MP3 128k mono | 70 min | ASR: 2-person interview with book mentions |
| produced | Planet Money: "the great international borrowing spree" | MP3 128k CBR | 26 min | ASR: heavily produced, many voices |
| long-mp3 | Lex Fridman #503 | MP3 96k, 48 kHz | 3 h 43 min | drift |
| aac-music | Adam Love DJ set | AAC 44.1k | 2 h | drift |
| aac-anchor | Multifamily Tech Talk (Spotify for Creators) | AAC 44.1k | 53 min | drift |
| aac-blubrry | Baseball PhD | AAC 32k, with chapter/artwork tracks | 44 min | drift |

## M0.1 ASR benchmark

`spike/bench_vm.py` runs on the VM and `spike/analyze.py` runs locally. Every stage ran on the
server's prepared 16 kHz Opus file, as the real pipeline will.

### 1. Decision

- **ASR: `faster-whisper` `large-v3-turbo`** (fp16, `BatchedInferencePipeline`, batch 16,
  Silero VAD) for every language.
- **Word timings: forced alignment with torchaudio's `MMS_FA`.** This is one multilingual
  wav2vec2 model, used instead of WhisperX's per-language models. It covered English and
  Spanish with no failures.
- **Parakeet is rejected.** Parakeet TDT 0.6B v3 is a little faster, but it silently dropped
  whole minutes of speech: 3 minutes on `music` (the speech around the song) and 8 minutes on
  `spanish`. A transcript with holes is worse than a slower one.
- **GPU: T4.** The whole pipeline (ASR + alignment + diarization) fits easily in 16 GB, and
  per audio hour the T4 is the cheaper choice (§3).

### 2. Accuracy

Whisper was scored against each publisher's transcript, normalised with Whisper's own text
normaliser. Two numbers are given because dynamically inserted ads are in the audio but not in
the publisher's text. Every ad word counts as an insertion, so the figure without insertions
(in brackets) is the fair one.

| Episode | Whisper WER | Parakeet WER | Whisper vs. Parakeet |
| --- | --- | --- | --- |
| produced | 3.2% (2.5%) | 3.5% (2.4%) | 3.9% |
| interview | 8.5% (2.4%) | 14.5% (2.5%) | 8.3% |
| music | 78.8% (**6.7%**) | 76.4% (**39.3%**) | 25.2% |
| spanish¹ | 25.8% (6.1%) | 32.6% (27.2%) | 32.4% |
| crosstalk | (no reference) | | 11.4% |

¹ Radio Ambulante's feed transcript is itself ASR output (it says "Rabia Ambulante"), so this
row compares two machine transcripts.

Spot-check notes:
- Proper nouns are the main English errors: "Rishikesh" for Hrishikesh, and "Introducing" for
  *Endtroducing*.
- Whisper produced one classic hallucination over the music outro on `spanish`:
  "¡Suscríbete al canal!". A worker-side filter for known hallucination strings on low-speech
  segments is cheap and worth adding in M2.1.
- **Language detection failed on `spanish`.** The episode opens with English ads, so
  auto-detection picked English. With the feed's `<language>es</language>` passed as a hint,
  the output was correct, and Whisper still transcribed the English ads in English. **Pass
  the feed's `<language>` (or a per-feed override) as the hint, and auto-detect only when the
  feed has none.**

### 3. Speed (excluding model load)

Real-time factor = audio minutes per GPU minute, over all 3.7 h of audio.

| Stage | T4 | L4 | Peak VRAM |
| --- | --- | --- | --- |
| Whisper turbo | 52× | 90× | 4.1–5.4 GB |
| MMS_FA alignment | 55× | 140× | 2.5–3.1 GB on its own² |
| pyannote `community-1` diarization | 22× | (not run) | 4.2 GB |
| (Sortformer, not chosen) | 155× | 228× | 1.2–4.2 GB |
| (Parakeet, rejected) | 66× | 130× | 11–14 GB |
| **Whole pipeline** (Whisper + align + pyannote, in sequence) | **≈ 12×** | (≈ 23×, estimated) | well under 16 GB |
| Without diarization (solo feeds) | ≈ 27× | ≈ 55× | |

² Higher numbers in `summary.md` include memory the earlier Whisper stage still held in the
same process.

Model load: Whisper 2–12 s and MMS_FA 3–12 s, once the weights are cached on the VM. A first
download on a fresh VM adds about a minute. For the §8.4 estimates, **one hour of audio takes
about 5 GPU-minutes on a T4 with diarization, and 2.2 without.** The L4 is about twice as fast, but
by Colab's published rates (not measured here) it bills at about 2.5 times the T4's rate, so
the T4 is cheaper per audio hour. M2.4 replaces
these numbers with measured `colab usage` rates.

### 4. Word timings

The hand-marked check (about 20 words per episode, timed by ear) is **not done yet**. The
seek probes on the M0.4 page (§M0.4) double as that check. Meanwhile, two *independent* timing
methods agree closely. MMS_FA forced alignment of Whisper's words and Parakeet's native TDT
timestamps were compared on every word both engines got right (n = 1,850–11,457 per episode):

| Pair | Median \|Δ start\| | p90 | Within 100 ms | Signed bias |
| --- | --- | --- | --- | --- |
| MMS-aligned vs. Parakeet | 59–72 ms | 150–200 ms | 64–75% | +14 to +28 ms |
| Whisper native vs. Parakeet | 80–100 ms | 200–350 ms | 49–58% | **−80 to −100 ms** |

Whisper's own word timestamps run about 90 ms early on average, and they spread more. The
alignment stage earns its 2 GPU-seconds per audio minute. Both aligned sources sit within a
few tens of milliseconds of each other, which is good enough for tap-to-seek and word
highlighting.

### 5. Segments

`BatchedInferencePipeline` returns VAD chunks, not sentences. The median segment is 27 s, and
one is 280 s, spanning the song on `music`. The worker must **re-segment using the word
timings**: split at sentence-final punctuation, at speaker changes and at pauses of 1 s or
more, with a 30 s cap. That gives the §5.1 segment shape. Do this before the speaker labels
are assigned, because the "segment purity" figures below were measured on the raw 27 s
chunks.

### 6. Diarization: pyannote `community-1`

pyannote 3.x can't be installed on the current Colab image (torchaudio 2.11 removed
`AudioMetaData`), so the pyannote candidate is the 4.x pipeline,
`pyannote/speaker-diarization-community-1`. It is gated, and your HF token has access to it.
(The token still gets a 403 on 3.1 and on `segmentation-3.0`, but neither is needed.) The
other candidate is NVIDIA's ungated `diar_streaming_sortformer_4spk-v2`.

`spike/diar_eval.py` gives each MMS-aligned word the speaker active at its midpoint. It then
matches the words to the speaker-labelled publisher transcripts and maps each cluster to the
reference speaker it overlaps most:

| Episode | Real speakers | Sortformer: clusters, word accuracy | pyannote: clusters, word accuracy |
| --- | --- | --- | --- |
| interview | 2 | 3, 98.6% | 2, 98.4% |
| music | 2 | 4, 99.2% | 4, 99.6% |
| produced | 13 | 4 (its maximum), 91.0% | 8, **94.4%** |
| crosstalk (no reference) | 3 | 4: talk shares 54/36/6/3% (two brothers merged) | 6: **45/31/22**/2% (all three found) |
| spanish (no reference) | many | 4: 71/17/6/6% | 18: 49/12/8/6/6/4% (probably over-split) |

**Decision: pyannote `community-1`.** The two are equal on 2-person shows. pyannote is better
on many-voice and similar-voice shows. It has no 4-speaker cap, and it keeps NeMo out of the
worker. NeMo caused every dependency fight in this spike: it pins lightning 2.4, needs a
numba-cuda bump, trips a signature check in `nv_one_logger`, and patches globals such as
`torch.load`, so later models in the same process fail to load.

The cost is speed. pyannote runs at **22×** on a T4, against Sortformer's 155×, so it becomes
the slowest stage. The pipeline drops from about 23× to about 12× on a T4: roughly 5
GPU-minutes per audio hour. Two ways to recover some of that: turn diarization off for solo
feeds (already a per-feed setting), and tune pyannote's embedding batch size in M2.1.
Over-splitting (18 clusters on the Spanish show) matters little, because labels are guesses
you can rename, and §5.1 carries names over by overlap.

### 7. Worker environment notes (Colab image of 2026-11)

- The image has Python 3.13, torch/torchaudio 2.11+cu130 and numpy 2.1.
- `ctranslate2` 4.8 needs CUDA 12 cuBLAS/cuDNN. `pip install nvidia-cublas-cu12` (cuDNN cu12 is
  already present), and preload the libraries with `ctypes.CDLL(..., RTLD_GLOBAL)` before
  importing `faster_whisper`.
- Install with `uv pip install --system -c constraints` that pin torch and torchaudio to the
  image's versions, or a resolver will happily replace CUDA torch.
- `pyannote.audio>=4` needs numpy ≥ 2.2. Upgrading numpy on the image was harmless for
  everything used here.
- `spike/setup_vm.py` is the full recipe that worked.

## M0.2 Colab round trip

`spike/roundtrip.py` uses colabkit v0.1.0 throughout.

| Step | CPU runtime |
| --- | --- |
| `colab new` | 3.6 s (T4: 4.5 s, L4: similar) |
| bootstrap `exec` (write the worker, `Popen` uvicorn) | 4.6–5.1 s |
| `SshTunnel.open()` | 1.2–3.1 s |
| **new → worker healthy** | **9.9–12.5 s** (no pip install; a real worker adds its install and model loads) |
| HTTP round trip through the tunnel | 52 ms (a US VM) to 227 ms (an asia-southeast VM) |
| `stop` | 1.2–2.9 s, and `status` confirms it's gone |

The unattended round trip works.

**Liveness:** after bootstrap, the kernel sat idle for **45 minutes** while the
SSH-launched worker kept a CPU busy and was polled over the tunnel every 2 minutes. The VM
stayed up and the worker kept answering (`spike/data/liveness.log`). This extends Degas's
inconclusive 15-minute result. **Decision: no `exec` heartbeat by default.** Keep it as a
setting (`keepalive_exec_minutes`, off). M2.4 logs every unexpected `SessionGone` with the
kernel's idle time, so a reclaim pattern would show up. A caveat: the test polled
`colab status` every 2 minutes, which may or may not count as activity to Colab.

**Maximum session lifetime:** not measured, because you chose to skip the 12–24 h run.
`max_session_hours` defaults to **10**. M2.4 records each session's age at reclaim, so the
real limit can be read from normal use.

Gotchas for M2.2, found along the way:
- A `colab exec` whose code prints nothing for a minute or so can return early, and its work
  doesn't complete. Long work must run in a detached process (`Popen(...,
  start_new_session=True)`), as the design already says. Never block inside `exec`.
- **`colab exec --env K=V` persists in the kernel.** Later `exec`s, and processes they
  `Popen`, still see `K`. Pass per-job settings in a file or on the command line, not through
  `--env`.
- `colab upload` to a directory that doesn't exist fails with an HTTP 500. Create the
  directory first.
- Twice, a `colab exec -f` run from a non-interactive background shell returned at once with
  exit 0 and no output. The same command worked in the foreground, and the colabkit-based
  round trip worked in the background. The cause is unknown. Don't trust a 0 exit with empty
  output (colabkit already judges results by their output).

## M0.3 Timeline drift

`spike/drift.py` transcodes with the §3 recipe (`-ac 1 -ar 16000 -c:a libopus -b:a 32k`),
decodes both files with ffmpeg, and cross-correlates 10 s windows every 5 minutes. The
resolution is sub-sample, finer than 0.1 ms.

| File | Length | Max \|offset\| | Drift first→last |
| --- | --- | --- | --- |
| 6 MP3s (incl. 3 h 43 min, 48 kHz) | 21 min – 3.7 h | ≤ 0.08 ms | ≤ 0.07 ms |
| 3 AACs (incl. 2 h; one with extra data/artwork tracks) | 44 min – 2 h | ≤ 0.03 ms | ≤ 0.02 ms |

**There is no drift, so no correction is needed.** Decoded lengths match exactly. The prepared
file is 12–17 MB per hour (as planned). Note: on LAME-tagged MP3s, ffprobe reports
`start_time` 0.012–0.025 s, which is encoder delay that ffmpeg trims. If Safari doesn't trim
it, the player is off by at most 25 ms, well inside the budget. M0.4's seek probes would show
anything larger.

## M0.4 iOS background playback: waiting on you

The spike is `spike/ios/` (a FastAPI app with Range support, one long-lived `<audio>`,
Media Session, and no service worker). It runs on **https://agda.tail0cb6d.ts.net:8452**,
running as the transient user unit `hearsay-spike-ios` (`systemd-run --user ... uv run
spike/ios/server.py`, so it outlives the shell that started it), with `tailscale serve --bg
--https=8452 http://127.0.0.1:8452`. Stop it with `systemctl --user stop hearsay-spike-ios`.

To do on the iPhone:
1. Open the URL in Safari, choose Share → Add to Home Screen, and open it from the icon.
2. Play the Lex Fridman episode (3.7 h, MP3), lock the phone, and leave it for 2 h. Switch to
   other apps a few times, take a call or invoke Siri once, and use the lock-screen controls.
3. Seek check: on Planet Money and MBMBaM, tap each word button. Playback starts 0.3 s before
   the word, and the word should be heard within the first second.
4. Repeat a short check with the AAC episode.

The page logs a heartbeat every 10 s while it's playing, plus play/pause/visibility events,
to `spike/data/ios_log.jsonl`. Gaps between `wall` and `ct` show whether playback kept running
while the phone was locked. Tell me when you've done it and I'll read the log and finish this
section.

## M0.5 Embedding model

`spike/embed_eval.py` chunks the M0.1 Whisper transcripts as §5.2 describes (45–60 s at
segment boundaries, a 1-segment overlap, and the show/title prefix). That gave 437 chunks.
It then embeds them on the home box's CPU (an i7-8550U, 4 cores and 8 threads), and scores
**32 labelled queries** (`spike/queries.toml`). These are mostly paraphrases with little
keyword overlap: 4 are about the Spanish episode, 3 of those written in English. A query is a
hit when a top-10 chunk overlaps the labelled span.

| Model | Recall@10 | MRR@10 | Query p50 (CPU) | Doc embedding / chunk | Dim |
| --- | --- | --- | --- | --- | --- |
| **`BAAI/bge-small-en-v1.5`** | **0.97** | 0.88 | **16 ms** | 90–160 ms | 384 |
| `intfloat/multilingual-e5-small` | 0.94 | 0.83 | 16–33 ms | 80–135 ms | 384 |
| `google/embeddinggemma-300m` | 0.97 | **0.94** | 455 ms (max 1.2 s) | 660 ms | 768 |
| `Qwen/Qwen3-Embedding-0.6B` | stopped | | | **over 4 s** (437 chunks didn't finish in 34 min) | 1024 |

**Decision: `BAAI/bge-small-en-v1.5` at revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`.** Query embedding takes 16 ms, against a 150 ms
budget. Indexing takes about 6–11 s of CPU per audio hour, so re-embedding a 1,000 h library
takes about 2–3 h, a background job of the kind §5.2 assumes. That same assumption doesn't
hold for Qwen3-0.6B: it would take about 5 CPU-minutes per audio hour, and days for a full
re-embed. EmbeddingGemma ranks best, but it's 30× slower per query than the budget allows on
this CPU.

Caveats:
- The corpus is small (5 episodes), so recall is near its ceiling for every model. These
  numbers rule out slow models; they don't rank the fast ones finely. M3.2's done-when check
  re-runs this query set, and you should add queries from your real library then.
- Every miss, for every model, was an English query about the Spanish episode
  (cross-lingual). The Spanish-language query hit for both small models. Keyword search still
  covers non-English shows. If the library ends up with a lot of non-English audio, revisit
  this with a quantized EmbeddingGemma or a multilingual model behind ONNX.

## Design changes made from these results

- §12: the HTTPS port is **8452**, not 8449. AisleFlow took 8449 after the design was written,
  and `~/workspace/TAILSCALE_PORTS.md` is updated.
- §5.1: the ASR stack is Whisper turbo + MMS_FA alignment, and Parakeet is dropped. The
  language hint comes from the feed, and segments are rebuilt from word timings. Diarization
  is pyannote `community-1`.
- §5.3: no keepalive by default, and `max_session_hours = 10`. Long work runs detached, never
  inside `exec`.
- §8.4: the starting real-time factor for the whole pipeline on a T4 is ≈ 12× (≈ 27× without
  diarization).
- §3: drift is measured, and none was found.
- §5.2: the embedding model is `bge-small-en-v1.5` at a pinned revision.

"""M0.1: ASR benchmark, run on a Colab VM via `colab exec -f bench_vm.py`.

Reads /content/bench/<id>.opus (the server's prepared files), writes
/content/bench/out/<id>.<gpu>.json, and prints one JSON line per stage.

Stages per episode:
  whisper  faster-whisper large-v3-turbo, fp16, batched, Silero VAD, native word timestamps
  align    torchaudio MMS_FA forced alignment of the whisper words (multilingual wav2vec2)
  parakeet nvidia/parakeet-tdt-0.6b-v3 (25 European languages), native word timestamps
  sortformer  nvidia/diar_streaming_sortformer_4spk-v2 (ungated, max 4 speakers)
  pyannote    pyannote speaker-diarization (gated; skipped if the HF token lacks access)

Env: BENCH_IDS (comma list, default all), BENCH_STAGES (default all), HF_TOKEN.
"""

import gc
import glob
import json
import os
import re
import subprocess
import threading
import time
import unicodedata

import numpy as np
import torch

SR = 16000
BENCH = "/content/bench"
OUT = f"{BENCH}/out"
os.makedirs(OUT, exist_ok=True)
GPU = torch.cuda.get_device_name(0).split()[-1]  # "T4", "L4"
STAGES = os.environ.get("BENCH_STAGES", "whisper,align,parakeet,sortformer,pyannote").split(",")


def log(**kw):
    print(json.dumps(kw), flush=True)


class VramPeak:
    """Polls nvidia-smi: CTranslate2 allocations are invisible to torch.cuda stats."""

    def __init__(self):
        self.peak = 0
        self._stop = threading.Event()

    def _used(self):
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True,
                             env={**os.environ, "LD_LIBRARY_PATH": "/usr/lib64-nvidia"}).stdout
        return int(out.split()[0]) if out.strip() else 0

    def __enter__(self):
        self.base = self._used()

        def loop():
            while not self._stop.wait(0.25):
                self.peak = max(self.peak, self._used())

        self._t = threading.Thread(target=loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join()


def free():
    gc.collect()
    torch.cuda.empty_cache()


def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


# ---------------------------------------------------------------- whisper

def load_cu12():
    import ctypes

    for pkg in ("cublas", "cudnn"):
        for so in sorted(glob.glob(f"/usr/local/lib/python3.13/dist-packages/nvidia/{pkg}/lib/*.so*")):
            try:
                ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass


def run_whisper(audio, language=None):
    load_cu12()
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    t0 = time.time()
    model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    pipe = BatchedInferencePipeline(model)
    load_s = time.time() - t0
    t0 = time.time()
    segs, info = pipe.transcribe(audio, batch_size=16, word_timestamps=True, vad_filter=True,
                                language=language)
    segments = [{"start": s.start, "end": s.end, "text": s.text.strip(),
                 "words": [{"w": w.word.strip(), "s": w.start, "e": w.end, "p": w.probability} for w in s.words]}
                for s in segs]
    run_s = time.time() - t0
    del pipe, model
    return {"language": info.language, "language_p": info.language_probability, "language_hint": language,
            "segments": segments,
            "load_s": load_s, "run_s": run_s}


# ---------------------------------------------------------------- MMS_FA alignment

def norm_word(w):
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^a-z']", "", w)


def run_align(audio, whisper):
    import torchaudio

    t0 = time.time()
    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model(with_star=False).to("cuda").eval()
    tokenizer, aligner = bundle.get_tokenizer(), bundle.get_aligner()
    load_s = time.time() - t0
    t0 = time.time()
    out, failed = [], 0
    for seg in whisper["segments"]:
        words = [w["w"] for w in seg["words"]]
        normed = [norm_word(w) for w in words]
        keep = [i for i, n in enumerate(normed) if n]
        a, b = max(0.0, seg["start"] - 0.5), min(len(audio) / SR, seg["end"] + 0.5)
        clip = torch.from_numpy(audio[int(a * SR):int(b * SR)]).unsqueeze(0).to("cuda")
        aligned = [None] * len(words)
        try:
            if not keep:
                raise ValueError("no alignable words")
            with torch.inference_mode():
                emission, _ = model(clip)
            spans = aligner(emission[0], tokenizer([normed[i] for i in keep]))
            ratio = clip.size(1) / emission.size(1) / SR
            for i, sp in zip(keep, spans):
                aligned[i] = (a + sp[0].start * ratio, a + sp[-1].end * ratio, float(np.mean([x.score for x in sp])))
        except Exception:
            failed += 1
            aligned = [(w["s"], w["e"], None) for w in seg["words"]]
        # words with no alignable characters (numbers, symbols): interpolate between neighbours
        for i in range(len(aligned)):
            if aligned[i] is None:
                prev_e = next((aligned[j][1] for j in range(i - 1, -1, -1) if aligned[j]), seg["start"])
                next_s = next((aligned[j][0] for j in range(i + 1, len(aligned)) if aligned[j]), seg["end"])
                aligned[i] = (prev_e, max(prev_e, next_s), None)
        out.append([{"w": w, "s": s, "e": e, "score": sc} for w, (s, e, sc) in zip(words, aligned)])
    run_s = time.time() - t0
    del model
    return {"words": out, "failed_segments": failed, "load_s": load_s, "run_s": run_s}


# ---------------------------------------------------------------- parakeet

def quiet_cuts(audio, every=300.0, search=5.0):
    """Cut points near every `every` s, at the quietest 100 ms frame within ±search s."""
    cuts, n, f = [0], len(audio), SR // 10
    t = every
    while t * SR < n - 30 * SR:
        lo, hi = int((t - search) * SR), int((t + search) * SR)
        e = np.square(audio[lo:hi][: (hi - lo) // f * f].reshape(-1, f)).mean(1)
        cuts.append(lo + int(np.argmin(e)) * f + f // 2)
        t = cuts[-1] / SR + every
    cuts.append(n)
    return cuts


def patch_nemo():
    # nemo 3.0's nv_one_logger trips `overrides`' signature check against lightning 2.6; spike-only bypass
    import sys

    import overrides  # noqa: F401  (the package re-exports a function named `overrides`)

    sys.modules["overrides.overrides"]._validate_method = lambda *a, **k: None


def run_parakeet(audio):
    patch_nemo()
    import nemo.collections.asr as nemo_asr

    t0 = time.time()
    model = nemo_asr.models.ASRModel.from_pretrained("nvidia/parakeet-tdt-0.6b-v3").to("cuda").eval()
    load_s = time.time() - t0
    t0 = time.time()
    cuts = quiet_cuts(audio)
    chunks = [audio[a:b] for a, b in zip(cuts, cuts[1:])]
    with torch.inference_mode():
        hyps = model.transcribe(chunks, batch_size=2, timestamps=True, verbose=False)
    words, text = [], []
    for a, h in zip(cuts, hyps):
        off = a / SR
        text.append(h.text)
        for w in h.timestamp["word"]:
            words.append({"w": w["word"], "s": off + w["start"], "e": off + w["end"]})
    run_s = time.time() - t0
    del model
    return {"text": " ".join(text), "words": words, "chunks": len(chunks), "load_s": load_s, "run_s": run_s}


# ---------------------------------------------------------------- diarization

def run_sortformer(wav_path):
    patch_nemo()
    from nemo.collections.asr.models import SortformerEncLabelModel

    t0 = time.time()
    model = SortformerEncLabelModel.from_pretrained("nvidia/diar_streaming_sortformer_4spk-v2").to("cuda").eval()
    # "high latency" offline-ish configuration from the model card
    m = model.sortformer_modules
    m.chunk_len, m.chunk_right_context, m.fifo_len, m.spkcache_update_period, m.spkcache_len = 340, 40, 40, 300, 188
    m._check_streaming_parameters()
    load_s = time.time() - t0
    t0 = time.time()
    with torch.inference_mode():
        res = model.diarize(audio=[wav_path], batch_size=1)
    turns = []
    for line in res[0]:
        s, e, spk = line.split()
        turns.append({"s": float(s), "e": float(e), "spk": spk})
    run_s = time.time() - t0
    del model
    return {"turns": turns, "speakers": sorted({t["spk"] for t in turns}), "load_s": load_s, "run_s": run_s}


def run_pyannote(audio):
    from pyannote.audio import Pipeline

    tok = os.environ.get("HF_TOKEN")
    last = None
    for name in ("pyannote/speaker-diarization-community-1", "pyannote/speaker-diarization-3.1"):
        try:
            t0 = time.time()
            pipe = Pipeline.from_pretrained(name, token=tok).to(torch.device("cuda"))
            break
        except Exception as e:  # gated, no access
            last = e
    else:
        return {"skipped": f"{type(last).__name__}: {str(last)[:200]}"}
    load_s = time.time() - t0
    t0 = time.time()
    out = pipe({"waveform": torch.from_numpy(audio).unsqueeze(0), "sample_rate": SR})
    ann = getattr(out, "speaker_diarization", out)
    turns = [{"s": t.start, "e": t.end, "spk": spk} for t, _, spk in ann.itertracks(yield_label=True)]
    run_s = time.time() - t0
    del pipe
    return {"model": name, "turns": turns, "speakers": sorted({t["spk"] for t in turns}),
            "load_s": load_s, "run_s": run_s}


# ---------------------------------------------------------------- main

def main():
    ids = os.environ.get("BENCH_IDS")
    paths = sorted(glob.glob(f"{BENCH}/*.opus"))
    if ids:
        paths = [p for p in paths if os.path.basename(p)[:-5] in ids.split(",")]
    for path in paths:
        ep = os.path.basename(path)[:-5]
        out_path = f"{OUT}/{ep}.{GPU}.json"
        res = json.load(open(out_path)) if os.path.exists(out_path) else {}
        audio = decode(path)
        dur = len(audio) / SR
        res.update({"episode": ep, "gpu": GPU, "duration_s": dur})
        wav = f"/tmp/{ep}.wav"
        for stage in STAGES:
            if stage in res and "error" not in res[stage]:
                continue
            free()
            try:
                with VramPeak() as vp:
                    if stage == "whisper":
                        lang = f"{BENCH}/{ep}.lang"
                        r = run_whisper(audio, open(lang).read().strip() if os.path.exists(lang) else None)
                    elif stage == "align":
                        r = run_align(audio, res["whisper"])
                    elif stage == "parakeet":
                        r = run_parakeet(audio)
                    elif stage == "sortformer":
                        if not os.path.exists(wav):
                            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-ac", "1", "-ar", str(SR), wav],
                                           check=True)
                        r = run_sortformer(wav)
                    elif stage == "pyannote":
                        r = run_pyannote(audio)
                    free()
                r["vram_peak_mb"] = vp.peak
                r["vram_base_mb"] = vp.base
                if "run_s" in r:
                    r["x_realtime"] = dur / r["run_s"]
            except Exception as e:
                import traceback

                r = {"error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-2000:]}
            res[stage] = r
            json.dump(res, open(out_path, "w"))
            log(ep=ep, gpu=GPU, stage=stage, **{k: (round(v, 2) if isinstance(v, float) else v)
                                                for k, v in r.items()
                                                if k in ("load_s", "run_s", "x_realtime", "vram_peak_mb",
                                                         "language", "speakers", "error", "skipped",
                                                         "failed_segments", "model")})


main()

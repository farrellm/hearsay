"""M0.1 analysis: WER, word-timing agreement, diarization, speed. Reads data/bench/*.json.

    uv run analyze.py > data/bench/summary.md
"""
# /// script
# dependencies = ["jiwer", "whisper-normalizer", "numpy"]
# ///
import difflib
import glob
import json
import pathlib
import re
import unicodedata

import jiwer
import numpy as np
from whisper_normalizer.basic import BasicTextNormalizer
from whisper_normalizer.english import EnglishTextNormalizer

D = pathlib.Path(__file__).parent / "data"
EN, BASIC = EnglishTextNormalizer(), BasicTextNormalizer()
# speakers you can hear, counted by hand from the publisher transcripts
TRUE_SPEAKERS = {"music": 2, "crosstalk": 3, "interview": 2, "produced": "5+", "spanish": "5+"}


def norm(text, lang):
    t = EN(text) if lang == "en" else BASIC(text)
    t = unicodedata.normalize("NFKD", t)
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c))).strip()


def wer(ref, hyp, lang):
    r, h = norm(ref, lang), norm(hyp, lang)
    return jiwer.wer(r, h) if r and h else float("nan")


def wer_parts(ref, hyp, lang):
    """(WER, WER without insertions). Ads in the audio but not in the publisher text are pure
    insertions, so the second number is the fairer accuracy figure."""
    o = jiwer.process_words(norm(ref, lang), norm(hyp, lang))
    n = o.hits + o.substitutions + o.deletions
    return o.wer, (o.substitutions + o.deletions) / n


def fmt2(p):
    return f"{p[0] * 100:.1f}% ({p[1] * 100:.1f}%)"


def tok(w):
    return re.sub(r"[^\w']", "", unicodedata.normalize("NFKD", w.lower()))


def timing_diffs(a, b):
    """Start-time differences (s) for words matched by exact token alignment between two word lists."""
    ta, tb = [tok(w["w"]) for w in a], [tok(w["w"]) for w in b]
    sm = difflib.SequenceMatcher(a=ta, b=tb, autojunk=False)
    out = []
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            wa, wb = a[blk.a + k], b[blk.b + k]
            if ta[blk.a + k]:
                out.append(wa["s"] - wb["s"])
    return np.array(out)


def stats(d):
    d = np.abs(d)
    if not len(d):
        return "n/a"
    return (f"median {np.median(d) * 1000:.0f} ms, p90 {np.percentile(d, 90) * 1000:.0f} ms, "
            f"<100 ms {np.mean(d < 0.1) * 100:.0f}% (n={len(d)})")


def speaker_purity(segments, turns):
    """Share of segment time that falls on the segment's majority speaker."""
    tot = maj = 0.0
    for s in segments:
        ov = {}
        for t in turns:
            o = min(s["end"], t["e"]) - max(s["start"], t["s"])
            if o > 0:
                ov[t["spk"]] = ov.get(t["spk"], 0) + o
        if ov:
            tot += sum(ov.values())
            maj += max(ov.values())
    return maj / tot if tot else float("nan")


def main():
    files = sorted(glob.glob(str(D / "bench" / "*.json")))
    speed, quality, timing, diar = [], [], [], []
    for f in files:
        r = json.load(open(f))
        ep, gpu, dur = r["episode"], r["gpu"], r["duration_s"]
        for stage in ("whisper", "align", "parakeet", "sortformer", "pyannote"):
            x = r.get(stage, {})
            if "run_s" in x:
                speed.append(f"| {ep} | {gpu} | {stage} | {dur / 60:.0f} | {x['run_s']:.0f} | {x['x_realtime']:.0f}× "
                             f"| {x['load_s']:.0f} | {x['vram_peak_mb'] / 1024:.1f} |")
            elif x:
                speed.append(f"| {ep} | {gpu} | {stage} | {dur / 60:.0f} | {(x.get('error') or x.get('skipped')).splitlines()[0][:60]} |||||")
        if gpu != "T4":
            continue  # quality is GPU-independent; report once
        w = r.get("whisper", {})
        lang = w.get("language", "en")
        wtext = " ".join(s["text"] for s in w.get("segments", []))
        ptext = r.get("parakeet", {}).get("text", "")
        ref_p = D / ep / "reference.txt"
        ref = ref_p.read_text() if ref_p.exists() else None
        seg_d = np.array([s["end"] - s["start"] for s in w.get("segments", [])])
        quality.append(
            f"| {ep} | {lang} (p={w.get('language_p', 0):.2f}, hint={w.get('language_hint')}) "
            f"| {fmt2(wer_parts(ref, wtext, lang))} | {fmt2(wer_parts(ref, ptext, lang))} "
            f"| {wer(wtext, ptext, lang) * 100:.1f}% | {len(seg_d)} segs, median {np.median(seg_d):.1f} s, "
            f"max {seg_d.max():.1f} s |" if ref else
            f"| {ep} | {lang} | – | – | {wer(wtext, ptext, lang) * 100:.1f}% | {len(seg_d)} segs |")
        native = [x for s in w.get("segments", []) for x in s["words"]]
        aligned = [x for seg in r.get("align", {}).get("words", []) for x in seg]
        para = r.get("parakeet", {}).get("words", [])
        if aligned and para:
            timing.append(f"| {ep} | MMS-aligned vs Parakeet | {stats(timing_diffs(aligned, para))} |")
            timing.append(f"| {ep} | Whisper native vs Parakeet | {stats(timing_diffs(native, para))} |")
            timing.append(f"| {ep} | Whisper native vs MMS-aligned | {stats(timing_diffs(native, aligned))} |")
        for name in ("sortformer", "pyannote"):
            x = r.get(name, {})
            if "turns" in x:
                talk = {}
                for t in x["turns"]:
                    talk[t["spk"]] = talk.get(t["spk"], 0) + t["e"] - t["s"]
                share = ", ".join(f"{v / sum(talk.values()) * 100:.0f}%" for v in sorted(talk.values(), reverse=True))
                diar.append(f"| {ep} | {name} | {TRUE_SPEAKERS.get(ep)} | {len(talk)} ({share}) "
                            f"| {speaker_purity(w.get('segments', []), x['turns']) * 100:.1f}% |")

    print("## Speed\n\n| episode | GPU | stage | audio min | run s | × real time | load s | peak VRAM GB |")
    print("|---|---|---|---|---|---|---|---|")
    print("\n".join(speed))
    print("\n## Text\n\nWER against the publisher transcript (normalised); in brackets, WER without insertions "
          "(dynamically inserted ads are in the audio but not in the publisher text). Last column: WER between the "
          "two engines.\n")
    print("| episode | language | Whisper WER | Parakeet WER | Whisper↔Parakeet | segments |\n|---|---|---|---|---|---|")
    print("\n".join(quality))
    print("\n## Word timing agreement (|Δ start| of identical words)\n\n| episode | pair | agreement |\n|---|---|---|")
    print("\n".join(timing))
    print("\n## Diarization\n\n| episode | model | real speakers | found (talk share) | segment purity |")
    print("|---|---|---|---|---|")
    print("\n".join(diar))


main()

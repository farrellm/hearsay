"""M0.1 diarization: word-level speaker accuracy against publisher transcripts that name speakers.

Each MMS-aligned Whisper word gets the diarization speaker active at its midpoint. Words are
matched to the speaker-labelled reference with difflib; each diarization cluster is mapped to
the reference speaker it overlaps most (many-to-one allowed, so over-splitting isn't
penalised here — the cluster count is reported separately). Accuracy = share of matched words
whose mapped speaker equals the reference speaker.

    uv run diar_eval.py
"""
# /// script
# dependencies = []
# ///
import collections
import difflib
import html
import json
import pathlib
import re
import subprocess
import unicodedata

D = pathlib.Path(__file__).parent / "data"


def tok(w):
    return re.sub(r"[^\w']", "", unicodedata.normalize("NFKD", w.lower()))


def words(text, spk):
    return [(t, spk) for t in (tok(w) for w in text.split()) if t]


def ref_interview():
    s = (D / "interview/page.html").read_text()
    s = s[s.find('id="audio-highlights"'):]
    s = s[:s.find("</table>")]
    out, spk = [], None
    for p in re.findall(r"<p>(.*?)</p>", s, flags=re.S):
        m = re.match(r"\s*<strong>([^<:]+):</strong>(.*)", p, flags=re.S)
        if m:
            spk, p = m.group(1).strip(), m.group(2)
        if spk:
            out += words(html.unescape(re.sub(r"<[^>]+>", " ", p)), spk)
    return out


def ref_produced():
    s = (D / "produced/page.html").read_text()
    s = s[s.find('class="transcript storytext'):]
    s = s[:s.find("</div>")]
    out = []
    for p in re.split(r"<p>", s):
        m = re.match(r"\s*([A-Z][A-Z .'\-]+):(.*)", html.unescape(re.sub(r"<[^>]+>", " ", p)), flags=re.S)
        if m:
            txt = re.sub(r"\([A-Z ,'\-]+\)", " ", m.group(2))
            out += words(txt, m.group(1).strip())
    return out


def ref_music():
    t = subprocess.run(["pdftotext", str(D / "music/ref.pdf"), "-"], capture_output=True, text=True).stdout
    out, spk = [], None
    for line in t.splitlines():
        m = re.match(r"^(\w+):\s*(.*)", line)
        if m:
            spk, line = m.group(1), m.group(2)
        if spk and not line.strip().startswith("("):
            out += words(line, spk)
    return out


def hyp(ep, model):
    r = json.loads((D / "bench" / f"{ep}.T4.json").read_text())
    turns = r[model]["turns"]
    out = []
    for seg in r["align"]["words"]:
        for w in seg:
            t = tok(w["w"])
            if not t:
                continue
            mid = (w["s"] + w["e"]) / 2
            spk = next((x["spk"] for x in turns if x["s"] <= mid <= x["e"]), None)
            out.append((t, spk))
    return out, turns


def score(ref, hy):
    sm = difflib.SequenceMatcher(a=[t for t, _ in ref], b=[t for t, _ in hy], autojunk=False)
    pairs = [(ref[b.a + k][1], hy[b.b + k][1]) for b in sm.get_matching_blocks() for k in range(b.size)]
    co = collections.Counter(pairs)
    mapping = {}
    for (r, h), n in co.most_common():
        mapping.setdefault(h, r)
    ok = sum(n for (r, h), n in co.items() if mapping.get(h) == r and h is not None)
    none = sum(n for (r, h), n in co.items() if h is None)
    return ok / len(pairs), none / len(pairs), len(pairs)


def main():
    refs = {"interview": ref_interview(), "produced": ref_produced(), "music": ref_music()}
    print("| episode | ref speakers | model | clusters | word speaker accuracy | unlabelled words | matched |")
    print("|---|---|---|---|---|---|---|")
    for ep, ref in refs.items():
        nref = len({s for _, s in ref})
        for model in ("sortformer", "pyannote"):
            hy, turns = hyp(ep, model)
            acc, none, n = score(ref, hy)
            print(f"| {ep} | {nref} | {model} | {len({t['spk'] for t in turns})} | {acc * 100:.1f}% "
                  f"| {none * 100:.1f}% | {n} |")
    for ep in ("crosstalk", "spanish"):
        r = json.loads((D / "bench" / f"{ep}.T4.json").read_text())
        for model in ("sortformer", "pyannote"):
            talk = collections.Counter()
            for t in r[model]["turns"]:
                talk[t["spk"]] += t["e"] - t["s"]
            tot = sum(talk.values())
            print(f"| {ep} | – | {model} | {len(talk)} | talk shares: "
                  + ", ".join(f"{v / tot * 100:.0f}%" for _, v in talk.most_common(6)) + " | | |")


main()

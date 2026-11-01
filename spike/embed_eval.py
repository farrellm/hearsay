"""M0.5: embedding model choice. Chunks the M0.1 Whisper transcripts the §5.2 way, embeds them on
this machine's CPU with each candidate, and scores the labelled queries in queries.toml.

    uv run embed_eval.py [model ...]
"""
# /// script
# dependencies = ["sentence-transformers", "numpy", "torch"]
# [[tool.uv.index]]
# url = "https://download.pytorch.org/whl/cpu"
# ///
import json
import pathlib
import statistics
import sys
import time
import tomllib

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

D = pathlib.Path(__file__).parent / "data"
CANDIDATES = {
    # model id: (query prefix or prompt_name, document prefix)
    "BAAI/bge-small-en-v1.5": ("Represent this sentence for searching relevant passages: ", ""),
    "intfloat/multilingual-e5-small": ("query: ", "passage: "),
    "Qwen/Qwen3-Embedding-0.6B": ("@query", ""),
    "google/embeddinggemma-300m": ("task: search result | query: ", "title: none | text: "),
}


def chunks(ep):
    r = json.loads((D / "bench" / f"{ep}.T4.json").read_text())
    meta = json.loads((D / ep / "meta.json").read_text())
    segs = r["whisper"]["segments"]
    out, i = [], 0
    while i < len(segs):
        j = i
        while j + 1 < len(segs) and segs[j + 1]["end"] - segs[i]["start"] <= 60:
            j += 1
        if segs[j]["end"] - segs[i]["start"] < 45 and j + 1 < len(segs):
            j += 1  # never under 45 s unless at the end
        text = " ".join(s["text"] for s in segs[i:j + 1])
        out.append({"ep": ep, "s": segs[i]["start"], "e": segs[j]["end"],
                    "text": f"{meta['show']} — {meta['title']}\n{text}"})
        if j + 1 >= len(segs):
            break
        i = max(i + 1, j)  # one-segment overlap
    return out


def main(models):
    qs = tomllib.loads((pathlib.Path(__file__).parent / "queries.toml").read_text())["q"]
    eps = sorted({q["ep"] for q in qs})
    docs = [c for ep in eps for c in chunks(ep)]
    print(f"{len(docs)} chunks from {len(eps)} episodes, {len(qs)} queries", flush=True)
    torch.set_num_threads(8)
    for mid in models:
        qp, dp = CANDIDATES[mid]
        t0 = time.time()
        m = SentenceTransformer(mid, device="cpu", trust_remote_code=True)
        m.max_seq_length = 512  # a 60 s chunk is ~200-250 tokens
        load_s = time.time() - t0
        t0 = time.time()
        dv = m.encode([dp + c["text"] for c in docs], batch_size=16, normalize_embeddings=True)
        doc_s = time.time() - t0
        lat, hits, rr = [], 0, 0.0
        m.encode([qp + "warm up"] if not qp.startswith("@") else ["warm up"], normalize_embeddings=True)
        for q in qs:
            t0 = time.time()
            if qp.startswith("@"):
                qv = m.encode([q["q"]], prompt_name=qp[1:], normalize_embeddings=True)[0]
            else:
                qv = m.encode([qp + q["q"]], normalize_embeddings=True)[0]
            lat.append(time.time() - t0)
            order = np.argsort(-(dv @ qv))[:10]
            rel = [k for k, i in enumerate(order)
                   if docs[i]["ep"] == q["ep"] and docs[i]["s"] < q["t1"] and docs[i]["e"] > q["t0"]]
            hits += bool(rel)
            if not rel or rel[0] > 2:
                print(f"  {mid.split('/')[-1]}: rank {rel[0] + 1 if rel else '>10'} for {q['q']!r}", flush=True)
            rr += 1 / (rel[0] + 1) if rel else 0
        row = {"model": mid, "recall@10": hits / len(qs), "mrr@10": rr / len(qs),
               "query_ms_p50": statistics.median(lat) * 1000, "query_ms_max": max(lat) * 1000,
               "doc_ms_per_chunk": doc_s / len(docs) * 1000, "load_s": load_s, "dim": dv.shape[1]}
        print(json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()}), flush=True)
        out = D / "bench" / "embed_eval.json"
        prev = [r for r in (json.loads(out.read_text()) if out.exists() else []) if r["model"] != mid]
        out.write_text(json.dumps(prev + [row], indent=1))
        del m



if __name__ == "__main__":
    main(sys.argv[1:] or list(CANDIDATES))

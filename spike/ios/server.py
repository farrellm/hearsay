"""M0.4: minimal PWA for iOS background-playback and seek checks.

    uv run ios/server.py            # 127.0.0.1:8452
    tailscale serve --bg --https=8452 http://127.0.0.1:8452

The page POSTs a heartbeat every 10 s while playing; they land in data/ios_log.jsonl so the
2 h locked-playback run can be checked afterwards (gaps in wall time vs. currentTime).
"""
# /// script
# dependencies = ["fastapi", "uvicorn"]
# ///
import json
import pathlib
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

HERE = pathlib.Path(__file__).parent
DATA = HERE.parent / "data"
LOG = DATA / "ios_log.jsonl"
EPISODES = ["produced", "crosstalk", "long-mp3", "aac-anchor"]
app = FastAPI()


@app.get("/")
def index():
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({"name": "Hearsay spike", "short_name": "Hearsay", "start_url": "/", "display": "standalone",
                         "background_color": "#111", "theme_color": "#111",
                         "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}]},
                        media_type="application/manifest+json")


@app.get("/icon.svg")
def icon():
    return FileResponse(HERE / "icon.svg")


@app.get("/api/episodes")
def episodes():
    out = []
    for ep in EPISODES:
        meta = json.loads((DATA / ep / "meta.json").read_text())
        probes = []
        bench = DATA / "bench" / f"{ep}.T4.json"
        if bench.exists():
            # a few aligned words spread over the episode, for the seek check
            words = [w for seg in json.loads(bench.read_text()).get("align", {}).get("words", []) for w in seg
                     if w.get("score") and len(w["w"]) > 5]
            probes = [{"w": w["w"], "s": round(w["s"], 2)} for w in words[:: max(1, len(words) // 8)]][:8]
        out.append({"id": ep, "title": meta["title"], "show": meta["show"], "type": meta["type"], "probes": probes})
    return out


@app.get("/audio/{ep}")
def audio(ep: str):
    assert ep in EPISODES
    meta = json.loads((DATA / ep / "meta.json").read_text())
    mt = "audio/mpeg" if meta["file"].endswith(".mp3") else "audio/mp4"
    return FileResponse(DATA / ep / meta["file"], media_type=mt)  # Starlette handles Range


@app.post("/api/log")
async def log(req: Request):
    rec = await req.json()
    rec["server_t"] = time.time()
    rec["ua"] = req.headers.get("user-agent", "")[:120]
    with LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8452)

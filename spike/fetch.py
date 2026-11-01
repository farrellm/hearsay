"""Download the Phase 0 episodes into spike/data/<id>/ (audio + meta.json + feed transcript)."""
# /// script
# dependencies = ["feedparser", "httpx"]
# ///
import json, sys, tomllib, pathlib, mimetypes
import feedparser, httpx

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
eps = tomllib.loads((ROOT / "episodes.toml").read_text())["ep"]
only = set(sys.argv[1:])
cli = httpx.Client(follow_redirects=True, timeout=120, headers={"User-Agent": "hearsay-spike/0"})
for ep in eps:
    if only and ep["id"] not in only:
        continue
    d = DATA / ep["id"]; d.mkdir(parents=True, exist_ok=True)
    f = feedparser.parse(cli.get(ep["feed"]).content)
    item = f.entries[ep["index"]] if "index" in ep else next(e for e in f.entries if ep["match"] in e.title)
    enc = next(l for l in item.links if l.get("rel") == "enclosure")
    ext = {"audio/mpeg": ".mp3"}.get(enc.get("type"), ".m4a")
    out = d / f"orig{ext}"
    if not out.exists():
        with cli.stream("GET", enc["href"]) as r:
            r.raise_for_status()
            tmp = out.with_suffix(".part")
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
            tmp.rename(out)
            final_url = str(r.url)
    else:
        final_url = None
    meta = {"id": ep["id"], "role": ep["role"], "show": f.feed.get("title"), "title": item.title,
            "published": item.get("published"), "enclosure": enc["href"], "final_url": final_url,
            "type": enc.get("type"), "file": out.name}
    tr = item.get("podcast_transcript")
    if tr:
        meta["feed_transcript"] = tr
        (d / "feed_transcript.txt").write_bytes(cli.get(tr["url"]).content)
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    print(ep["id"], out.stat().st_size // 1_000_000, "MB", item.title)

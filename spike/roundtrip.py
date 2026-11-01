"""M0.2: unattended colabkit round trip, plus an idle-kernel liveness check.

new -> tunnel -> Popen a trivial uvicorn worker from the kernel -> call it over the tunnel ->
(optionally) keep only the worker busy for LIVENESS_MIN minutes with no kernel activity -> stop.

    uv run roundtrip.py [--gpu T4] [--liveness-min 20]
"""
# /// script
# dependencies = ["colabkit @ https://github.com/farrellm/colabkit/archive/refs/tags/v0.1.0.tar.gz", "httpx"]
# ///
import argparse
import asyncio
import json
import pathlib
import subprocess
import time

import httpx
from colabkit import AsyncColabCli, SshTunnel

ROOT = pathlib.Path(__file__).parent
KEY = ROOT / "data" / "ssh" / "id_ed25519"
SESSION = "hearsay-rt"
PORT = 8765

WORKER = r'''
import time, threading
from fastapi import FastAPI
app = FastAPI()
state = {"busy_until": 0.0, "ticks": 0}
def spin():
    while True:
        if time.time() < state["busy_until"]:
            sum(i * i for i in range(200_000)); state["ticks"] += 1
        else:
            time.sleep(0.5)
threading.Thread(target=spin, daemon=True).start()
@app.get("/health")
def health(): return {"ok": True, "ticks": state["ticks"], "busy": time.time() < state["busy_until"]}
@app.post("/busy/{minutes}")
def busy(minutes: float):
    state["busy_until"] = time.time() + minutes * 60; return {"until": state["busy_until"]}
'''

BOOT = f'''
import subprocess, pathlib
pathlib.Path("/content/rt").mkdir(exist_ok=True)
pathlib.Path("/content/rt/worker.py").write_text({WORKER!r})
p = subprocess.Popen(["python", "-m", "uvicorn", "worker:app", "--host", "127.0.0.1", "--port", "{PORT}"],
                     cwd="/content/rt", stdout=open("/content/rt/worker.log", "a"), stderr=subprocess.STDOUT,
                     start_new_session=True)
print("@@pid", p.pid)
'''


def log(**kw):
    print(json.dumps({"t": time.strftime("%H:%M:%S"), **kw}), flush=True)


async def main(gpu: str | None, liveness_min: float) -> None:
    if not KEY.exists():
        KEY.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "hearsay-spike", "-f", str(KEY)],
                       check=True)
    cli = AsyncColabCli()
    t0 = time.monotonic()
    await cli.new(SESSION, gpu)
    log(step="new", s=round(time.monotonic() - t0, 1), gpu=gpu)
    try:
        t1 = time.monotonic()
        out = await cli.exec(SESSION, BOOT, timeout=120)
        log(step="bootstrap", s=round(time.monotonic() - t1, 1), out=out.strip()[-80:])
        tunnel = SshTunnel(cli.proxy_command(SESSION, str(KEY)), KEY, remote_port=PORT, name=SESSION)
        t1 = time.monotonic()
        await tunnel.open()
        log(step="tunnel", s=round(time.monotonic() - t1, 1), local_port=tunnel.local_port)
        base = f"http://127.0.0.1:{tunnel.local_port}"
        async with httpx.AsyncClient(base_url=base, timeout=10) as http:
            for _ in range(60):
                try:
                    r = await http.get("/health")
                    if r.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
            log(step="ready", total_s=round(time.monotonic() - t0, 1), health=r.json())
            t1 = time.monotonic()
            for _ in range(20):
                await http.get("/health")
            log(step="rtt", ms=round((time.monotonic() - t1) / 20 * 1000))
            if liveness_min:
                await http.post(f"/busy/{liveness_min}")
                end = time.monotonic() + liveness_min * 60
                while time.monotonic() < end:
                    await asyncio.sleep(120)
                    alive = await cli.status(SESSION)
                    try:
                        h = (await http.get("/health")).json()
                    except httpx.HTTPError as e:
                        h = {"error": type(e).__name__}
                    log(step="liveness", min=round(liveness_min - (end - time.monotonic()) / 60, 1),
                        session=alive, worker=h)
        await tunnel.close()
    finally:
        t1 = time.monotonic()
        await cli.stop(SESSION)
        log(step="stop", s=round(time.monotonic() - t1, 1), still_listed=await cli.status(SESSION))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpu")
    ap.add_argument("--liveness-min", type=float, default=0)
    a = ap.parse_args()
    asyncio.run(main(a.gpu, a.liveness_min))

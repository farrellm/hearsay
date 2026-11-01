# Hearsay

Personal podcast app: an iPhone PWA over Tailscale that downloads your RSS feeds, transcribes
episodes on Google Colab GPUs (via [colabkit](https://github.com/farrellm/colabkit)), and
makes them searchable, with a player tied to the transcript.

- Design and phase plan: [docs/design.md](docs/design.md)
- UI and UX: [docs/ui.md](docs/ui.md)
- Phase 0 results: [docs/spikes.md](docs/spikes.md)

## Layout

| Path | What |
|---|---|
| `server/` | Home server (FastAPI + SQLite), package `hearsay`: `api/` routers and error statuses · `db/` schema migrations and queries · `feeds/` RSS, OPML and the poller · `media/` downloads, ffprobe and the artwork cache · `tasks.py` the task queue's stages and runner · `config.py` · `services.py` wiring |
| `worker/` | GPU worker that runs on the Colab VM, package `hearsay_worker` (`spec.py`: the transcript types both sides share) |
| `web/` | React + Vite PWA: `src/app` shell · `src/features/<part>` screens · `src/components`, `src/hooks`, `src/lib` shared · `src/api` typed client and query options · `src/styles` tokens |
| `deploy/` | systemd user unit |
| `spike/` | Throwaway Phase 0 scripts, kept for reference (not linted) |

## Development

```sh
make tools     # pinned tools into ./bin, uv sync, pnpm install, git hooks
make check     # everything CI runs
make fmt       # format in place
make build     # build the PWA into web/dist (the server serves it)
```

## Running

1. `cp hearsay.toml.example hearsay.toml` and adjust.
2. `make enable` builds the PWA, installs `deploy/hearsay.service`, and starts it now and at
   every boot: the server listens on `127.0.0.1:8452` and `tailscale serve --https=8452`
   publishes it on the tailnet. Starting at boot needs lingering
   (`sudo loginctl enable-linger $USER`). `make disable` stops it and removes it from boot,
   `make deploy` rebuilds and restarts it, and `make logs` follows its journal.
3. On the iPhone, open `https://<host>.ts.net:8452` in Safari, then Share → **Add to Home
   Screen**.

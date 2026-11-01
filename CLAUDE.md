# Hearsay

Personal podcast PWA (iPhone over Tailscale); ASR on Colab GPUs via colabkit, batch sessions.
Read `README.md` for layout, `docs/design.md` for architecture and the milestones (§13),
`docs/ui.md` for tokens, vocabulary and screens. Read both docs before starting a milestone.
Conventions follow `~/workspace/degas`; look there first for a pattern (session manager,
worker client, fakes for the colab CLI, web tests).

## Commands

```sh
make fmt          # format in place (hooks only check)
make check        # fmt-check + lint + test + gitleaks; run before declaring done (CI adds `make build`)
uv run ruff check . && uv run mypy && uv run pytest   # Python only
uv run pytest server/tests/test_db.py -k name         # single Python test
cd web && pnpm vitest run src/app/App.test.tsx        # single web test
```

## Gotchas

- Schema changes are new entries appended to `MIGRATIONS` in `server/src/hearsay/db/schema.py`
  (`PRAGMA user_version` counts them). Never edit a shipped migration.
- `hearsay.app:create_app` is a factory (no module-level app). The static mount sends
  extensionless non-API paths to `index.html`, so deep links (`/e/<id>?t=`) cold-start.
- `worker/src/hearsay_worker` must never import `hearsay`; `spec.py` is shared.
- HTTPS port 8452 is Hearsay's origin (`~/workspace/TAILSCALE_PORTS.md`); the home-screen
  install is tied to it. Never start a second server on the real `hearsay.toml`: use a
  scratch config with its own `data_dir` and `port`.
- `spike/` is a frozen Phase 0 record — excluded from lint; don't edit.
- The prod service serves `web/dist` from disk, so `make build` changes what the phone gets.

## Workflow

- Milestone work is committed as `M<n>.<m>: <title>`; update the status line in
  `docs/design.md` when a milestone lands.

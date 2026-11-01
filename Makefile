.PHONY: help tools fmt fmt-check lint test check build dev-server dev-web install-unit deploy enable disable logs

SHELL := bash
.SHELLFLAGS := -euo pipefail -c

BIN := $(CURDIR)/bin
export PATH := $(BIN):$(PATH)

# Pinned tool versions; Renovate bumps them via the comments.
# renovate: datasource=github-releases depName=gitleaks/gitleaks
GITLEAKS_VERSION   := v8.30.1
# renovate: datasource=github-releases depName=evilmartians/lefthook
LEFTHOOK_VERSION   := v2.2.2
# renovate: datasource=github-releases depName=koalaman/shellcheck
SHELLCHECK_VERSION := v0.11.0
# renovate: datasource=github-releases depName=mvdan/sh
SHFMT_VERSION      := v3.14.1
# renovate: datasource=pypi depName=pip-audit
PIP_AUDIT_VERSION  := 2.10.1

PRETTIER := web/node_modules/.bin/prettier
SH_FILES := $(shell git ls-files --cached --others --exclude-standard '*.sh')

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

# ── Setup ─────────────────────────────────────────────────────────────────────

web/node_modules: web/pnpm-lock.yaml
	cd web && pnpm install --frozen-lockfile
	@touch $@

tools: ## Install pinned tools into ./bin, the Python and web packages, and the git hooks
	@mkdir -p $(BIN)
	curl -sSfL https://github.com/gitleaks/gitleaks/releases/download/$(GITLEAKS_VERSION)/gitleaks_$(GITLEAKS_VERSION:v%=%)_linux_x64.tar.gz \
		| tar -xz -C $(BIN) gitleaks
	curl -sSfL https://github.com/evilmartians/lefthook/releases/download/$(LEFTHOOK_VERSION)/lefthook_$(LEFTHOOK_VERSION:v%=%)_Linux_x86_64.gz \
		| gunzip > $(BIN)/lefthook
	curl -sSfL https://github.com/koalaman/shellcheck/releases/download/$(SHELLCHECK_VERSION)/shellcheck-$(SHELLCHECK_VERSION).linux.x86_64.tar.xz \
		| tar -xJ -C $(BIN) --strip-components=1 shellcheck-$(SHELLCHECK_VERSION)/shellcheck
	curl -sSfL -o $(BIN)/shfmt \
		https://github.com/mvdan/sh/releases/download/$(SHFMT_VERSION)/shfmt_$(SHFMT_VERSION)_linux_amd64
	chmod +x $(BIN)/lefthook $(BIN)/shfmt
	uv sync --locked
	cd web && pnpm install --frozen-lockfile
	lefthook install

# ── Checks ────────────────────────────────────────────────────────────────────

fmt: web/node_modules ## Format everything in place
	uv run ruff format .
	uv run ruff check --fix .
	$(PRETTIER) --write --log-level warn .
	$(if $(SH_FILES),shfmt -w $(SH_FILES))

fmt-check: web/node_modules ## Fail if anything needs formatting
	uv run ruff format --check .
	$(PRETTIER) --check --log-level warn .
	$(if $(SH_FILES),shfmt -d $(SH_FILES))

lint: web/node_modules ## Run every linter and type checker
	uv run ruff check .
	uv run mypy
	uv export --frozen --all-packages --no-emit-workspace -q \
		| uvx pip-audit==$(PIP_AUDIT_VERSION) --disable-pip --require-hashes -r /dev/stdin --progress-spinner off
	cd web && pnpm -s lint
	$(if $(SH_FILES),shellcheck $(SH_FILES))

test: web/node_modules ## Run the Python and web tests
	uv run pytest
	cd web && pnpm -s test

# Not `build`: the prod server serves web/dist straight from disk.
check: fmt-check lint test ## Everything CI runs
	gitleaks git --no-banner --redact .

# ── Run ───────────────────────────────────────────────────────────────────────

build: web/node_modules ## Build the PWA into web/dist (live at once: the server serves it)
	cd web && pnpm build

dev-server: ## Run the API with reload on 127.0.0.1:8452
	uv run uvicorn --factory hearsay.app:create_app --reload --host 127.0.0.1 --port 8452

dev-web: web/node_modules ## Run the Vite dev server (proxies /api to the API)
	cd web && pnpm dev

# ── Deploy ────────────────────────────────────────────────────────────────────

# Production: a systemd user unit runs the server (which also serves web/dist)
# on 127.0.0.1:8452 and publishes it with `tailscale serve --https=8452`.
# `enable` makes it start at boot: a user unit starts with the user's manager,
# which runs from boot only with lingering on (`sudo loginctl enable-linger $USER`).
UNIT_DIR := $(HOME)/.config/systemd/user

install-unit:
	install -Dm644 deploy/hearsay.service $(UNIT_DIR)/hearsay.service
	systemctl --user daemon-reload

deploy: build install-unit ## Build, sync the venv, and restart the systemd user unit
	uv sync --frozen
	systemctl --user restart hearsay

enable: build install-unit ## Install the unit, start it now and at every boot
	uv sync --frozen
	systemctl --user enable --now hearsay
	@loginctl show-user "$$USER" -p Linger | grep -q '=yes' \
		|| echo "Lingering is off, so it won't start until you log in: sudo loginctl enable-linger $$USER"

disable: ## Stop the unit and keep it from starting at boot
	systemctl --user disable --now hearsay

logs: ## Follow the deployed unit's logs
	journalctl --user -u hearsay -f

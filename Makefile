# Redirector task runner (EPIC-07 task 1). POSIX sh only.
# Windows: run these inside Git Bash (ships with Git for Windows).
# Every target is safe to re-run; `test` needs backend deps + node modules.

SHELL := /bin/sh
.PHONY: dev lint test e2e build smoke

dev: ## Boot the full stack locally (needs REDIRECTOR_ADMIN_PASSWORD + REDIRECTOR_JWT_SECRET in env)
	docker compose -f docker/compose.prod.yml up -d --build

lint: ## Backend + frontend lint gates (same as CI)
	python -m ruff check backend/
	python -m mypy backend/
	python -m flake8 backend/ tests/ --select=E9,F63,F7,F82 --count
	cd frontend && npm run typecheck

test: ## Backend pytest + frontend vitest
	python -m pytest tests/ -q
	cd frontend && npm test

e2e: ## End-to-end smoke against the prod stack (ephemeral credentials)
	mkdir -p /tmp/smoke-data && chmod 777 /tmp/smoke-data
	export REDIRECTOR_ADMIN_PASSWORD=$$(openssl rand -hex 16); \
	export REDIRECTOR_JWT_SECRET=$$(openssl rand -hex 32); \
	export DATA_DIR=/tmp/smoke-data; \
	docker compose -f docker/compose.prod.yml up -d --build
	for _ in $$(seq 1 30); do \
		curl -sf http://localhost:80/healthz && break || sleep 4; \
	done
	curl -sf http://localhost:80/healthz
	curl -sf http://localhost:80/app/ -o /dev/null
	export DATA_DIR=/tmp/smoke-data; \
	docker compose -f docker/compose.prod.yml down

build: ## Production builds (backend image + SPA dist)
	docker build -f docker/Dockerfile.api -t redirector-api:local .
	cd frontend && npm ci && npm run build

smoke: ## Backend load smoke (10 VUs, 60s, zero-failure gate)
	sh load_testing/load-smoke.sh

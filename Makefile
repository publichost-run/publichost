.PHONY: help \
  local-install local-run local-test local-lint local-format local-api local-worker local-beat \
  prod-deploy prod-up prod-down prod-logs prod-ps prod-pull prod-nginx-reload

HOST ?= publichost.run
SSH_PORT ?= 2200
SSH_USER ?= saydullo
REMOTE_PATH ?= /var/backends/publichost.run
SSH := ssh -p $(SSH_PORT) $(SSH_USER)@$(HOST)

help:
	@echo "Local:  local-install local-run local-test local-lint local-format local-api local-worker local-beat"
	@echo "Prod:   prod-deploy prod-up prod-down prod-logs prod-ps prod-pull prod-nginx-reload"

local-install:
	uv sync

local-run:
	uv run python -m publichost

local-test:
	uv run pytest -q

local-lint:
	uv run ruff check

local-format:
	uv run ruff format

local-api:
	uv run publichost-api

local-worker:
	uv run publichost-worker

local-beat:
	uv run publichost-beat

prod-deploy:
	$(SSH) "cd $(REMOTE_PATH) && git pull && docker compose down && docker compose up -d --build"

prod-up:
	$(SSH) "cd $(REMOTE_PATH) && docker compose up -d --build"

prod-down:
	$(SSH) "cd $(REMOTE_PATH) && docker compose down"

prod-logs:
	$(SSH) "cd $(REMOTE_PATH) && docker compose logs -f"

prod-ps:
	$(SSH) "cd $(REMOTE_PATH) && docker compose ps"

prod-pull:
	$(SSH) "cd $(REMOTE_PATH) && git pull"

prod-nginx-reload:
	$(SSH) "sudo nginx -t && sudo systemctl reload nginx"

.PHONY: help \
  local-install local-run local-test local-lint local-format local-api local-worker local-beat \
  deploy deploy-update deploy-status deploy-logs deploy-down

help:
	@echo "Local:  local-install local-run local-test local-lint local-format local-api local-worker local-beat"
	@echo "Server: deploy deploy-update deploy-status deploy-logs deploy-down (wrap ./deploy.sh)"

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

deploy:
	./deploy.sh

deploy-update:
	./deploy.sh update

deploy-status:
	./deploy.sh status

deploy-logs:
	./deploy.sh logs

deploy-down:
	./deploy.sh down

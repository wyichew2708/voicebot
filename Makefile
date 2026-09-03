.PHONY: setup dev run test models clean kb-ingest kb-lint kb-status kb-sources kb-ask

VENV := .venv
PY   := $(VENV)/bin/python
PORT ?= 8788

setup:                ## create the venv and install the app (no models)
	uv venv --python 3.11
	uv pip install -e ".[dev]"
	@echo "Ready. 'make run' starts the console in mock mode."

mlx:                  ## install Apple Silicon inference extras
	uv pip install -e ".[mlx]"
	$(PY) -m spacy download en_core_web_sm
	@echo "MLX ready. 'make models' downloads weights, then 'make mac'."

run:                  ## console with no models — UI and state machine only
	$(PY) -m voicebot.server --profile mock --port $(PORT)

mac:                  ## console with the real MLX models (see: make models)
	$(PY) -m voicebot.server --profile mac --port $(PORT)

models:               ## pre-download MLX weights so a demo never waits
	./scripts/fetch_models.sh

test:
	$(PY) -m pytest tests -q

eval:                 ## replay every recorded call through the engine (keyword layer)
	$(PY) scripts/eval.py

eval-live:            ## the same, with the models in the loop — reports guardrail latency
	$(PY) scripts/eval.py --live

# ------------------------------------------------------------- knowledge base
# The OKF bundle in knowledge/. See docs/knowledge-layer.md.

kb-ingest:            ## re-extract source documents into knowledge/raw/
	$(PY) scripts/kb_ingest.py

kb-check:             ## have any ingested source documents changed underneath us?
	$(PY) scripts/kb_ingest.py --check

kb-lint:              ## the gate: citations, locators, jurisdiction, figures, links
	$(PY) scripts/kb.py lint

kb-status:            ## which pages are approved and which are not
	$(PY) scripts/kb.py status

kb-sources:           ## the ingested documents, with hashes
	$(PY) scripts/kb.py sources

kb-ask:               ## answer a question the way a call would:  make kb-ask Q="free look" PROFILE=rhel
	$(PY) scripts/kb.py ask "$(Q)" $(if $(PROFILE),--profile $(PROFILE),) $(if $(LANG_),--lang $(LANG_),)

clean:
	rm -rf $(VENV) .pytest_cache **/__pycache__

meralion-setup:       ## separate venv for MERaLiON (needs transformers 4.x)
	uv venv .venv-meralion --python 3.11
	uv pip install --python .venv-meralion/bin/python meralion-3-asr "transformers<5" fastapi "uvicorn[standard]"

meralion:             ## run the MERaLiON ASR sidecar on :8799
	.venv-meralion/bin/python scripts/meralion_sidecar.py --port 8801

install:              ## install as a launchd agent (starts at login)
	./deploy/install.sh

uninstall:            ## remove the launchd agent
	./deploy/uninstall.sh

status:               ## is the deployed console up?
	@launchctl list | grep com.voicebot.console || echo "agent not loaded"
	@curl -s --max-time 3 http://127.0.0.1:8788/api/health || echo "not responding"

logs:                 ## tail the deployed console log
	tail -f logs/console.log

prerender:            ## render scripted turns with Qwen3-TTS into voices/cache
	$(PY) scripts/prerender.py --profile mac-polyglot

# ---------------------------------------------------------------- RHEL / CUDA
rhel-setup:           ## one-time host prep: driver, CDI, SELinux, firewall
	./deploy/rhel/setup.sh

rhel-up:              ## start the GPU services (ASR, TTS) in podman
	./deploy/rhel/services.sh

rhel:                 ## run the console against the GPU services
	$(PY) -m voicebot.server --profile rhel --port $(PORT)

rhel-check:           ## are the GPU services actually serving the right models?
	@curl -s --max-time 5 http://127.0.0.1:$(PORT)/api/health || echo "console not running"

# ---------------------------------------------------------------- containers
up:                   ## docker compose: build and start everything
	docker compose up -d --build

down:                 ## stop and remove the stack
	docker compose down

ps:                   ## what is running
	docker compose ps

clogs:                ## follow container logs
	docker compose logs -f --tail=100

console-only:         ## just the console, against services already running
	docker compose up -d --build console

name-audit:   ## render candidate spellings of a surname and build a page to listen to
	$(PY) scripts/name_audit.py $(NAMES) $(if $(NAMES),,--from-personas)
	@echo "open voices/audit/index.html"

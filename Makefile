.PHONY: setup dev run test models clean kb-ingest kb-lint kb-status kb-sources kb-ask \
        tts-models tts-say tts-engines tts-deps tts-sidecar tts-build tts-bench

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

# ------------------------------------------------------------ TTS candidates
# Trying another TTS model. Each engine is its own sidecar process (or image)
# behind the same /tts contract; the console never learns which one it is
# talking to. See docs/tts-models.md.
TTS_ENGINE ?= chatterbox
TTS_PORT   ?= 8802
PROFILE    ?= mac-polyglot

tts-models:           ## the switchable models (config/tts-models.yaml) and whether each runs here
	$(PY) scripts/tts_say.py --list --profile $(PROFILE)

tts-say:              ## one line in one model:  make tts-say MODEL=cosyvoice3 TEXT="Good afternoon Mr Tan."
	$(PY) scripts/tts_say.py --profile $(PROFILE) --model $(MODEL) --text "$(TEXT)" $(if $(LANG_),--lang $(LANG_),) $(if $(VOICE),--voice $(VOICE),) --play

tts-engines:          ## list the engines the GPU sidecar can serve
	$(PY) scripts/tts_sidecar.py --list-engines

tts-deps:             ## install one engine's dependencies into this venv:  make tts-deps TTS_ENGINE=f5
	PIP="$(VENV)/bin/pip" TTS_ENGINE_PREFIX=$(CURDIR)/models ./scripts/tts_engine_deps.sh $(TTS_ENGINE)

tts-sidecar:          ## run the sidecar here with one engine:  make tts-sidecar TTS_ENGINE=cosyvoice3 TTS_PORT=8803
	COSYVOICE_HOME=$(CURDIR)/models/CosyVoice INDEXTTS_HOME=$(CURDIR)/models/index-tts \
	FISH_HOME=$(CURDIR)/models/fish-speech VIBEVOICE_HOME=$(CURDIR)/models/VibeVoice \
	$(PY) scripts/tts_sidecar.py --engine $(TTS_ENGINE) --port $(TTS_PORT)

tts-build:            ## build the GPU sidecar image for one engine:  make tts-build TTS_ENGINE=cosyvoice3
	docker build -f Dockerfile.tts --build-arg TTS_ENGINE=$(TTS_ENGINE) -t voicebot-tts:$(TTS_ENGINE) .

tts-bench:            ## the Singapore insurance sentence set through models or sidecars:
	##   make tts-bench MODELS="chatterbox cosyvoice3 kokoro"        (config/tts-models.yaml, this profile)
	##   make tts-bench TARGETS="cosyvoice3=http://127.0.0.1:8803"   (a sidecar by address)
	$(PY) scripts/tts_bench.py --profile $(PROFILE) $(foreach m,$(MODELS),--model $(m)) $(TARGETS) $(BENCH_ARGS)
	@echo "open voices/bench/latest/index.html"

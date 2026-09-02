# The console. Deliberately CPU-only and small: on the GPU deployment this
# process loads no models — it talks HTTP to vLLM and the TTS sidecar, so it
# stays quick to build, quick to restart, and independent of CUDA versions.
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Dependencies first so edits to the app do not invalidate the layer.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e . && pip cache purge

COPY config/ ./config/
COPY ui/ ./ui/
COPY scripts/ ./scripts/

# voices/ is mounted rather than copied: the pre-rendered cache is ~28 MB of
# audio that changes when the script or voice changes, not when the code does.
VOLUME ["/app/voices"]

EXPOSE 8788
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8788/api/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "voicebot.server", "--profile", "rhel", \
     "--host", "0.0.0.0", "--port", "8788"]

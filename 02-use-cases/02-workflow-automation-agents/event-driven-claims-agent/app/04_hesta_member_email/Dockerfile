# The YAML-first pipeline as an AgentCore Runtime container.
#
# Built from this directory (agentcore.json: build "Container", codeLocation app/04_hesta_member_email, entrypoint main.py).
# core/ is client-neutral and ships in every image. hesta/ is the client package. An optional client_pack/ (a client's rule
# pack, copied in by scripts/assemble_client.py) is included when present; it is only USED when CEDAR_PACK_DIR points at it.
#
# Dependencies come from uv.lock exactly (--frozen), so the image runs the versions the tests ran against.

FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-trixie AS builder

WORKDIR /app

RUN pip install --no-cache-dir uv

# Dependency files first, for layer caching.
COPY pyproject.toml uv.lock ./

# The cedar extra is always installed: core/cedar_pack.py needs it whenever a client's pack is switched on, and it is small.
RUN uv sync --frozen --no-dev --no-install-project --extra cedar

# --- Runtime stage ---
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim-trixie

WORKDIR /app

# Non-root user for runtime
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY . /app
RUN chown -R appuser:appuser /app

USER appuser

# AgentCore Runtime uses port 8080
EXPOSE 8080

# GET /ping is the built-in health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping', timeout=3)" || exit 1

# Run with OTEL auto-instrumentation (aws-opentelemetry-distro)
CMD ["opentelemetry-instrument", "python", "main.py"]

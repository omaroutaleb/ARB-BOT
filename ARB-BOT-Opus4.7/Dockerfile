# syntax=docker/dockerfile:1.7

# ----- Stage 1: builder -----
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Install build deps only in this stage
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip wheel && \
    pip wheel --wheel-dir /wheels \
        aiohttp httpx websockets "python-socketio[asyncio_client]" \
        py-clob-client \
        web3 eth-account eth-utils eth-abi \
        pydantic pydantic-settings python-dotenv \
        structlog prometheus-client orjson tenacity

# ----- Stage 2: runtime -----
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Non-root user
RUN groupadd --system --gid 1001 arbbot && \
    useradd --system --uid 1001 --gid arbbot --no-create-home --shell /usr/sbin/nologin arbbot

WORKDIR /app

# Install only the wheels — no compilers, no apt cache, no build tools
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/*.whl && \
    rm -rf /wheels

# Copy source
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY pyproject.toml ./

# State volume target — owned by arbbot
RUN mkdir -p /app/state && chown -R arbbot:arbbot /app

USER arbbot

EXPOSE 9090

# Healthcheck — confirm metrics endpoint is alive
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://localhost:9090/metrics',timeout=3).status==200 else 1)"

ENTRYPOINT ["python", "-u", "-m", "src.main"]

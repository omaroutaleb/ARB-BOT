FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir aiohttp==3.9.5

COPY math_core.py selftest.py discover.py logger.py analyze.py entrypoint.sh ./
RUN chmod +x entrypoint.sh && \
    groupadd --system --gid 1001 logger && \
    useradd --system --uid 1001 --gid logger --no-create-home --shell /usr/sbin/nologin logger && \
    mkdir -p /app/data && chown -R logger:logger /app

USER logger

# Default entrypoint runs discovery once at start, then the logger loop. The
# logger also reloads pairs.json hourly so we don't need a cron.
ENTRYPOINT ["./entrypoint.sh"]

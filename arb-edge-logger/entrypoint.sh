#!/bin/sh
set -e
echo "[entrypoint] running self-test first"
python -m selftest
echo "[entrypoint] running initial discovery"
python -m discover
echo "[entrypoint] starting hourly discovery refresh loop in background"
(
    while true; do
        sleep 3600
        echo "[refresh] running discovery"
        python -m discover || echo "[refresh] discovery failed, continuing"
    done
) &
echo "[entrypoint] starting logger loop"
exec python -m logger

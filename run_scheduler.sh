#!/bin/sh
set -eu

INTERVAL_SECONDS="${RUN_INTERVAL_SECONDS:-21600}"
SCRAPER="${SCRAPER:-steel}"

echo "[INFO] Scheduler started: interval=${INTERVAL_SECONDS}s, scraper=${SCRAPER}"

while true; do
  start_ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "[INFO] Cycle start at ${start_ts}"

  if python txt.py && python main.py "${SCRAPER}"; then
    echo "[INFO] Cycle succeeded at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  else
    echo "[WARN] Cycle failed at $(date -u +"%Y-%m-%dT%H:%M:%SZ"), retry next cycle"
  fi

  echo "[INFO] Sleeping ${INTERVAL_SECONDS}s"
  sleep "${INTERVAL_SECONDS}"
done

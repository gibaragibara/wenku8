#!/bin/sh
set -eu

INTERVAL_SECONDS="${RUN_INTERVAL_SECONDS:-21600}"
SCRAPER="${SCRAPER:-steel}"
ENABLE_ONEDRIVE_UPLOAD="${ENABLE_ONEDRIVE_UPLOAD:-false}"
ONEDRIVE_REMOTE_TARGET="${ONEDRIVE_REMOTE_TARGET:-}"
ONEDRIVE_SYNC_PID=""

echo "[INFO] Scheduler started: interval=${INTERVAL_SECONDS}s, scraper=${SCRAPER}"

cleanup_bg() {
  if [ -n "${ONEDRIVE_SYNC_PID}" ] && kill -0 "${ONEDRIVE_SYNC_PID}" 2>/dev/null; then
    kill "${ONEDRIVE_SYNC_PID}" 2>/dev/null || true
  fi
}

start_onedrive_sync() {
  if [ "${ENABLE_ONEDRIVE_UPLOAD}" != "true" ]; then
    return 0
  fi
  if [ -z "${ONEDRIVE_REMOTE_TARGET}" ]; then
    echo "[WARN] ENABLE_ONEDRIVE_UPLOAD=true but ONEDRIVE_REMOTE_TARGET is empty, skip OneDrive upload worker"
    return 0
  fi
  if [ -n "${ONEDRIVE_SYNC_PID}" ] && kill -0 "${ONEDRIVE_SYNC_PID}" 2>/dev/null; then
    return 0
  fi
  echo "[INFO] Starting OneDrive upload worker"
  python -m lanzou_epub_downloader.onedrive_sync &
  ONEDRIVE_SYNC_PID="$!"
  echo "[INFO] OneDrive upload worker pid=${ONEDRIVE_SYNC_PID}"
}

trap cleanup_bg EXIT INT TERM

start_onedrive_sync

while true; do
  start_ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "[INFO] Cycle start at ${start_ts}"

  start_onedrive_sync

  if python txt.py && python main.py "${SCRAPER}"; then
    echo "[INFO] Cycle succeeded at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  else
    echo "[WARN] Cycle failed at $(date -u +"%Y-%m-%dT%H:%M:%SZ"), retry next cycle"
  fi

  echo "[INFO] Sleeping ${INTERVAL_SECONDS}s"
  sleep "${INTERVAL_SECONDS}"
done

#!/bin/sh
set -eu

INTERVAL_SECONDS="${RUN_INTERVAL_SECONDS:-21600}"
SCRAPER="${SCRAPER:-steel}"
ENABLE_ONEDRIVE_UPLOAD="${ENABLE_ONEDRIVE_UPLOAD:-false}"
ONEDRIVE_REMOTE_TARGET="${ONEDRIVE_REMOTE_TARGET:-}"
# Hard caps so a stuck Steel/Lanzou run cannot pin CPU indefinitely.
SCRAPE_TIMEOUT_SECONDS="${SCRAPE_TIMEOUT_SECONDS:-1800}"
LANZOU_RUN_TIMEOUT_SECONDS="${LANZOU_RUN_TIMEOUT_SECONDS:-600}"
export LANZOU_RUN_TIMEOUT_SECONDS
RUN_ONCE="${RUN_ONCE:-false}"
ONEDRIVE_SYNC_PID=""

echo "[INFO] Scheduler started: interval=${INTERVAL_SECONDS}s, scraper=${SCRAPER}, scrape_timeout=${SCRAPE_TIMEOUT_SECONDS}s, lanzou_timeout=${LANZOU_RUN_TIMEOUT_SECONDS}s"

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

run_with_timeout() {
  # usage: run_with_timeout SECONDS label cmd...
  _secs="$1"
  _label="$2"
  shift 2
  if command -v timeout >/dev/null 2>&1; then
    _rc=0
    if timeout --help 2>&1 | grep -q -- '--kill-after'; then
      timeout -s TERM -k 30s "${_secs}s" "$@" || _rc=$?
    else
      timeout "${_secs}" "$@" || _rc=$?
    fi
    if [ "${_rc}" -eq 124 ] || [ "${_rc}" -eq 137 ]; then
      echo "[WARN] ${_label} timed out after ${_secs}s (rc=${_rc})"
    fi
    return "${_rc}"
  fi
  echo "[WARN] timeout(1) not available; running ${_label} without hard cap"
  "$@"
}

trap cleanup_bg EXIT INT TERM

start_onedrive_sync

while true; do
  start_ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "[INFO] Cycle start at ${start_ts}"

  start_onedrive_sync

  cycle_ok=0
  if run_with_timeout "${SCRAPE_TIMEOUT_SECONDS}" "scrape(txt.py+main.py)" \
      sh -c 'python txt.py && python main.py "$1"' _ "${SCRAPER}"; then
    cycle_ok=1
  fi

  if [ "${cycle_ok}" -eq 1 ]; then
    echo "[INFO] Cycle succeeded at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  else
    echo "[WARN] Cycle failed at $(date -u +"%Y-%m-%dT%H:%M:%SZ"), retry next cycle"
  fi

  if [ "${RUN_ONCE}" = "true" ]; then
    break
  fi

  echo "[INFO] Sleeping ${INTERVAL_SECONDS}s"
  sleep "${INTERVAL_SECONDS}"
done

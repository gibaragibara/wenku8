#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Iterable, Tuple

from lanzou_epub_downloader.downloader import strip_label_prefix


def read_bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


BASE_DIR = Path(os.getenv("APP_BASE_DIR", "/app"))
OUT_DIR = Path(os.getenv("OUT_DIR", str(BASE_DIR / "out")))
DOWNLOAD_DIR = Path(os.getenv("LZ_DOWNLOAD_OUTPUT_DIR", str(OUT_DIR / "downloads")))
MERGED_CSV = Path(os.getenv("MERGED_CSV_PATH", str(OUT_DIR / "merged.csv")))
EPUB_DIR = DOWNLOAD_DIR / "epubs"
ARCHIVE_DIR = DOWNLOAD_DIR / "archives"
STATE_PATH = DOWNLOAD_DIR / "state.json"
ORGANIZED_DIR = Path(os.getenv("ONEDRIVE_ORGANIZED_DIR", str(DOWNLOAD_DIR / "_organized_by_main")))
LOG_PATH = Path(os.getenv("ONEDRIVE_SYNC_LOG", str(DOWNLOAD_DIR / "onedrive_sync.log")))
RCLONE_BIN = os.getenv("RCLONE_BIN", "rclone")
REMOTE_TARGET = os.getenv("ONEDRIVE_REMOTE_TARGET", "").strip()
REMOTE_ROOT = os.getenv("ONEDRIVE_REMOTE_ROOT", "").strip()
SYNC_INTERVAL_SECONDS = max(30, int(os.getenv("ONEDRIVE_UPLOAD_INTERVAL_SECONDS", "120")))
TRANSFERS = max(1, int(os.getenv("ONEDRIVE_UPLOAD_TRANSFERS", "4")))
CHECKERS = max(1, int(os.getenv("ONEDRIVE_UPLOAD_CHECKERS", "8")))
ROOT_CLEANUP_ENABLED = read_bool_env("ONEDRIVE_CLEAN_REMOTE_ROOT_DUPLICATES", True)
RCLONE_COMMAND_TIMEOUT_SECONDS = max(60, int(os.getenv("RCLONE_COMMAND_TIMEOUT_SECONDS", "900")))


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def safe_name(name: str) -> str:
    value = (name or "").strip()
    for ch in '\\/:*?"<>|':
        value = value.replace(ch, "_")
    value = " ".join(value.split())
    return value or "untitled"


def derive_remote_root(remote_target: str) -> str:
    if "/" not in remote_target:
        return remote_target
    return remote_target.rsplit("/", 1)[0]


def load_state() -> Dict[str, dict]:
    for _ in range(3):
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            time.sleep(1)
    return {"labels": {}}


def build_label_to_main() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not MERGED_CSV.exists():
        return mapping
    with MERGED_CSV.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            label = (row.get("dl_label") or "").strip()
            main = (row.get("main") or row.get("title") or "").strip()
            if label and main and label not in mapping:
                mapping[label] = main
    return mapping


def ensure_organized_tree(state: Dict[str, dict], label_to_main: Dict[str, str]) -> int:
    ORGANIZED_DIR.mkdir(parents=True, exist_ok=True)
    linked = 0
    for label, payload in (state.get("labels") or {}).items():
        main = label_to_main.get(label)
        if not main:
            continue
        folder = ORGANIZED_DIR / safe_name(main)
        folder.mkdir(parents=True, exist_ok=True)
        for epub_raw in payload.get("epubs") or []:
            src = Path(epub_raw)
            if not src.exists():
                continue
            dst = folder / strip_label_prefix(src.name, label)
            if dst.exists():
                continue
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
            linked += 1
    return linked


def run_rclone(args: Iterable[str]) -> Tuple[int, str, str]:
    cmd = [RCLONE_BIN] + list(args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=RCLONE_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        err = (err + "\n" if err else "") + (
            f"rclone command timed out after {RCLONE_COMMAND_TIMEOUT_SECONDS}s: {' '.join(cmd)}"
        )
        return 124, out, err
    return proc.returncode, proc.stdout, proc.stderr


def upload_cycle() -> bool:
    rc, out, err = run_rclone(
        [
            "copy",
            str(ORGANIZED_DIR),
            REMOTE_TARGET,
            "--transfers",
            str(TRANSFERS),
            "--checkers",
            str(CHECKERS),
            "--retries",
            "6",
            "--low-level-retries",
            "20",
            "--stats",
            "30s",
            "--stats-one-line",
            "--log-level",
            "INFO",
        ]
    )
    if out.strip():
        log("[UPLOAD][STDOUT]\n" + out.strip())
    if err.strip():
        log("[UPLOAD][STDERR]\n" + err.strip())
    return rc == 0


def remote_index() -> Dict[str, int]:
    rc, out, err = run_rclone(["lsjson", "-R", "--files-only", REMOTE_TARGET])
    if rc != 0:
        if err.strip():
            log("[REMOTE_INDEX_ERR] " + err.strip())
        return {}
    data = json.loads(out or "[]")
    return {
        item["Path"]: item.get("Size", -1)
        for item in data
        if isinstance(item, dict) and item.get("Path")
    }


def join_remote(remote: str, *parts: str) -> str:
    base = remote.rstrip("/")
    suffix = "/".join(part.strip("/") for part in parts if part)
    return f"{base}/{suffix}" if suffix else base


def remote_index_for_local_files(state: Dict[str, dict], label_to_main: Dict[str, str]) -> Dict[str, int]:
    folders = set()
    for label, payload in (state.get("labels") or {}).items():
        main = label_to_main.get(label)
        if not main:
            continue
        if any(Path(p).exists() for p in (payload.get("epubs") or []) if p):
            folders.add(safe_name(main))

    remote_map: Dict[str, int] = {}
    for folder in sorted(folders):
        rc, out, err = run_rclone(["lsjson", "--files-only", join_remote(REMOTE_TARGET, folder)])
        if rc != 0:
            if err.strip() and "directory not found" not in err.lower():
                log(f"[REMOTE_FOLDER_ERR] {folder} {err.strip()}")
            continue
        try:
            data = json.loads(out or "[]")
        except Exception as exc:
            log(f"[REMOTE_FOLDER_PARSE_ERR] {folder} {exc}")
            continue
        for item in data:
            name = item.get("Name") or item.get("Path") or ""
            if not name:
                continue
            remote_map[f"{folder}/{Path(name).name}"] = item.get("Size", -1)
    return remote_map


def cleanup_remote_root_duplicates(remote_map: Dict[str, int]) -> int:
    if not ROOT_CLEANUP_ENABLED:
        return 0
    rc, out, err = run_rclone(["lsjson", "--files-only", REMOTE_ROOT])
    if rc != 0:
        if err.strip():
            log("[REMOTE_ROOT_ERR] " + err.strip())
        return 0
    data = json.loads(out or "[]")
    basenames = {
        Path(path).name
        for path, size in remote_map.items()
        if isinstance(size, int) and size > 0
    }
    deleted = 0
    for item in data:
        name = item.get("Name") or ""
        if not name.lower().endswith(".epub"):
            continue
        if name not in basenames:
            continue
        del_rc, _, del_err = run_rclone(["deletefile", f"{REMOTE_ROOT}/{name}"])
        if del_rc == 0:
            deleted += 1
            log(f"[REMOTE_ROOT_CLEANED] {name}")
        elif del_err.strip():
            log(f"[REMOTE_ROOT_CLEAN_ERR] {name} {del_err.strip()}")
    return deleted


def prune_local(state: Dict[str, dict], label_to_main: Dict[str, str], remote_map: Dict[str, int]) -> Tuple[int, int]:
    deleted_epubs = 0
    deleted_archives = 0
    for label, payload in (state.get("labels") or {}).items():
        main = label_to_main.get(label)
        if not main:
            continue
        folder_name = safe_name(main)
        local_epubs = [Path(p) for p in (payload.get("epubs") or []) if p]
        if not local_epubs:
            continue

        checks = []
        for src in local_epubs:
            if not src.exists():
                continue
            dest_name = strip_label_prefix(src.name, label)
            remote_size = remote_map.get(f"{folder_name}/{dest_name}")
            if remote_size is None:
                remote_size = remote_map.get(f"{folder_name}/{src.name}")
            if remote_size is None or remote_size != src.stat().st_size:
                checks = []
                break
            checks.append((src, ORGANIZED_DIR / folder_name / dest_name))

        if not checks:
            continue

        for src, organized in checks:
            try:
                if organized.exists():
                    organized.unlink()
                if src.exists():
                    src.unlink()
                    deleted_epubs += 1
            except Exception as exc:
                log(f"[LOCAL_DELETE_ERR] {src} {exc}")

        archive_paths = [p for p in (payload.get("archive_paths") or []) if p]
        archive_raw = payload.get("archive_path") or ""
        if archive_raw and archive_raw not in archive_paths:
            archive_paths.insert(0, archive_raw)
        for archive_raw in archive_paths:
            archive_path = Path(archive_raw)
            try:
                if archive_path.exists():
                    archive_path.unlink()
                    deleted_archives += 1
            except Exception as exc:
                log(f"[ARCHIVE_DELETE_ERR] {archive_path} {exc}")

        try:
            organized_folder = ORGANIZED_DIR / folder_name
            if organized_folder.exists() and not any(organized_folder.iterdir()):
                organized_folder.rmdir()
        except Exception:
            pass

    return deleted_epubs, deleted_archives


def count_local_epubs() -> int:
    return sum(1 for _ in EPUB_DIR.glob("*.epub"))


def main() -> int:
    if not REMOTE_TARGET:
        log("[WARN] ONEDRIVE_REMOTE_TARGET is empty, skip onedrive sync worker.")
        return 0

    remote_root = REMOTE_ROOT or derive_remote_root(REMOTE_TARGET)
    globals()["REMOTE_ROOT"] = remote_root

    log(
        "[START] onedrive sync worker started "
        f"remote_target={REMOTE_TARGET} remote_root={REMOTE_ROOT} interval={SYNC_INTERVAL_SECONDS}s"
    )

    while True:
        state = load_state()
        label_to_main = build_label_to_main()
        linked = ensure_organized_tree(state, label_to_main)
        if linked:
            log(f"[ORGANIZE] linked_new={linked}")

        upload_ok = upload_cycle()
        remote_map = remote_index_for_local_files(state, label_to_main) if upload_ok else {}
        if remote_map:
            cleaned = cleanup_remote_root_duplicates(remote_map)
            if cleaned:
                log(f"[REMOTE_ROOT] cleaned_duplicates={cleaned}")
            deleted_epubs, deleted_archives = prune_local(state, label_to_main, remote_map)
            if deleted_epubs or deleted_archives:
                log(f"[PRUNE] deleted_epubs={deleted_epubs} deleted_archives={deleted_archives}")

        remaining = count_local_epubs()
        log(f"[STATUS] local_epubs={remaining}")
        time.sleep(SYNC_INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())

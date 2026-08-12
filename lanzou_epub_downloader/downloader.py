#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import html as html_lib
import json
import os
import random
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlparse

import requests


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "*/*",
    }
)


def log(message: str) -> None:
    print(message, flush=True)


def safe_filename(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "untitled"


def is_zht_name(name: str) -> bool:
    value = (name or "").strip().lower()
    return value.startswith("zht_") or "繁体" in value


def unique_path(base_dir: Path, filename: str) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    candidate = base_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    i = 2
    while True:
        nxt = base_dir / f"{stem}_{i}{suffix}"
        if not nxt.exists():
            return nxt
        i += 1


def safe_unlink(path: Optional[Path]) -> bool:
    if not path:
        return False
    try:
        p = Path(path)
        if p.is_file():
            p.unlink()
            return True
    except Exception:
        return False
    return False


def is_generic_bundle_name(name: str) -> bool:
    stem = Path(name or "").stem.lower()
    return stem == "合集" or stem.startswith("合集_") or stem in {"collection", "bundle", "pack"}


def archive_name_for_label(label: str, original_name: str) -> str:
    """Stable name per (label, 合集序号): b04euduna_合集1.zip / b04euduna_合集2.zip."""
    label_safe = safe_filename(label or "unknown")
    orig = Path(original_name or "合集.zip")
    stem = safe_filename(orig.stem) or "合集"
    suffix = orig.suffix.lower() if orig.suffix else ".zip"
    if suffix not in {".zip", ".7z", ".rar", ".epub"}:
        suffix = ".zip"
    # Keep 合集1/合集2/合集3 distinct under the same share label
    return f"{label_safe}_{stem}{suffix}"


def finalize_download_path(
    path: Path,
    download_dir: Path,
    label: str,
    original_name: str = "",
) -> Path:
    """Rename download to {label}_{合集N}.zip so multi-bundle shares don't collide."""
    if not path or not path.exists():
        return path
    label = (label or "").strip()
    if not label:
        return path
    src_name = original_name or path.name
    preferred = download_dir / archive_name_for_label(label, src_name)
    if path.resolve() == preferred.resolve():
        return path
    try:
        if preferred.exists():
            preferred.unlink()
        path.replace(preferred)
        return preferred
    except Exception:
        try:
            shutil.copy2(path, preferred)
            path.unlink(missing_ok=True)
            return preferred
        except Exception:
            return path


def cleanup_local_files(paths: Iterable[Path], reason: str = "") -> int:
    deleted = 0
    for raw in paths:
        p = Path(raw)
        if safe_unlink(p):
            deleted += 1
    if deleted and reason:
        log(f"[INFO] 已删除本地文件 {deleted} 个 ({reason})")
    return deleted


def referenced_local_paths(state: Dict[str, dict]) -> set:
    refs = set()
    for payload in (state.get("labels") or {}).values():
        for key in ("archive_path",):
            val = payload.get(key) or ""
            if val:
                refs.add(str(Path(val)))
        for key in ("archive_paths", "epubs"):
            for val in payload.get(key) or []:
                if val:
                    refs.add(str(Path(val)))
    return refs


def cleanup_orphan_archives(archive_dir: Path, state: Dict[str, dict]) -> int:
    """Remove leftover generic 合集 / 合集_N piles and archives no longer referenced."""
    if not archive_dir.exists():
        return 0
    refs = referenced_local_paths(state)
    ref_names = {Path(r).name for r in refs}
    deleted = 0
    for path in list(archive_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        name_l = name.lower()
        if not name_l.endswith((".zip", ".7z", ".rar", ".epub")):
            continue
        # Always drop generic collision names from older builds (合集.zip / 合集_27.zip)
        if is_generic_bundle_name(name):
            if safe_unlink(path):
                deleted += 1
            continue
        # Drop archives not referenced by state (already extracted & pruned)
        if name_l.endswith((".zip", ".7z", ".rar")) and name not in ref_names:
            if safe_unlink(path):
                deleted += 1
    if deleted:
        log(f"[INFO] 清理残留 archives={deleted}")
    return deleted


def parse_prefix(dl_txt_path: Path) -> str:
    first_line = dl_txt_path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    m = re.search(r"网址前缀：([^<\s]+)", first_line)
    if not m:
        raise ValueError(f"无法从 {dl_txt_path} 第一行解析蓝奏前缀")
    return m.group(1).strip().replace("https://", "").replace("http://", "").rstrip("/")


def load_all_labels(merged_csv_path: Path) -> List[str]:
    labels: List[str] = []
    seen = set()
    with merged_csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            label = (row.get("dl_label") or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            labels.append(label)
    return labels


def build_entry_signature(entry: Dict[str, str]) -> str:
    payload = {
        "title": (entry.get("title") or "").strip(),
        "volume": (entry.get("volume") or "").strip(),
        "update": (entry.get("update") or "").strip(),
        "remark": (entry.get("remark") or "").strip(),
        "pwd": (entry.get("pwd") or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def load_all_entry_signatures(merged_csv_path: Path) -> Dict[str, str]:
    signatures: Dict[str, str] = {}
    with merged_csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            label = (row.get("dl_label") or "").strip()
            if not label or label in signatures:
                continue
            entry = {
                "title": (row.get("main") or row.get("title") or label).strip(),
                "volume": (row.get("volume") or "").strip(),
                "label": label,
                "pwd": (row.get("dl_pwd") or "").strip(),
                "update": (row.get("dl_update") or row.get("update") or "").strip(),
                "remark": (row.get("dl_remark") or "").strip(),
            }
            signatures[label] = build_entry_signature(entry)
    return signatures


def load_entries(merged_csv_path: Path, limit: int = 0, name_contains: str = "") -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    needle = name_contains.strip().lower()
    with merged_csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            label = (row.get("dl_label") or "").strip()
            if not label:
                continue
            title = (row.get("main") or row.get("title") or label).strip()
            if needle and needle not in title.lower():
                continue
            entries.append(
                {
                    "title": title,
                    "volume": (row.get("volume") or "").strip(),
                    "label": label,
                    "pwd": (row.get("dl_pwd") or "").strip(),
                    "update": (row.get("dl_update") or row.get("update") or "").strip(),
                    "remark": (row.get("dl_remark") or "").strip(),
                }
            )
            if limit > 0 and len(entries) >= limit:
                break
    return entries


def load_state(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {"labels": {}, "baseline_labels": [], "baseline_entries": {}, "baseline_created_at": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid state")
        data.setdefault("labels", {})
        data.setdefault("baseline_labels", [])
        data.setdefault("baseline_entries", {})
        data.setdefault("baseline_created_at", "")
        return data
    except Exception:
        return {"labels": {}, "baseline_labels": [], "baseline_entries": {}, "baseline_created_at": ""}


def save_state(path: Path, state: Dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def first_locator(scope, selectors):
    for sel in selectors:
        loc = scope.locator(sel)
        if loc.count() > 0:
            return loc.first
    return None


def all_scopes(page):
    scopes = [page]
    for fr in page.frames:
        if fr != page.main_frame:
            scopes.append(fr)
    return scopes


def first_locator_any_scope(page, selectors):
    for scope in all_scopes(page):
        loc = first_locator(scope, selectors)
        if loc is not None:
            return loc
    return None


def timeout_left_ms(deadline_ts: float) -> int:
    """Milliseconds remaining until deadline. Returns 0 when expired.

    Callers must treat 0 as stop now. Never inflate remaining time above
    the real deadline (the old min_ms=1 default caused post-deadline retries).
    """
    left = int((deadline_ts - time.monotonic()) * 1000)
    if left <= 0:
        return 0
    return left


def bounded_timeout_s(timeout_ms: int, *, min_s: float = 1.0, max_s: float = 60.0) -> float:
    """Convert timeout_ms to seconds for requests/playwright."""
    if timeout_ms is None or timeout_ms <= 0:
        return 0.05
    seconds = float(timeout_ms) / 1000.0
    if seconds < min_s:
        return max(0.05, seconds)
    return min(max_s, seconds)


def remaining_timeout_ms(deadline_ts: float, cap_ms: int) -> int:
    """Return a timeout that cannot extend an existing operation deadline."""
    return min(timeout_left_ms(deadline_ts), max(0, int(cap_ms)))


def navigation_timeout_cap_ms() -> int:
    """Max Playwright navigation budget for a single goto attempt.

    Lanzou (and CDN frontends) often need >30s from a 1-core VPS; the old hard
    30s cap produced repeated `Page.goto: Timeout 30000ms exceeded` failures.
    """
    try:
        value = int(os.getenv("LANZOU_NAV_TIMEOUT_MS", "90000"))
    except (TypeError, ValueError):
        value = 90000
    return max(15000, min(value, 180000))


def navigation_timeout_ms(deadline_ts: float, cap_ms: Optional[int] = None) -> int:
    """Navigation timeout clamped by both the entry deadline and nav cap."""
    return remaining_timeout_ms(
        deadline_ts,
        navigation_timeout_cap_ms() if cap_ms is None else max(0, int(cap_ms)),
    )


def is_target_closed_error(err) -> bool:
    msg = str(err).lower()
    return (
        ("target page" in msg and "has been closed" in msg)
        or ("context or browser has been closed" in msg)
        or ("target closed" in msg)
    )


def is_navigation_timeout_error(err) -> bool:
    msg = str(err).lower()
    return ("timeout" in msg and ("goto" in msg or "navigation" in msg or "page.goto" in msg)) or (
        "exceeded" in msg and "timeout" in msg
    )


def page_looks_loaded(page) -> bool:
    """Best-effort check that a timed-out navigation still produced a usable page."""
    try:
        url = (page.url or "").strip().lower()
    except Exception:
        url = ""
    if url and url not in {"about:blank", "chrome-error://chromewebdata/"}:
        try:
            content = page.content()
        except Exception:
            content = ""
        if content and len(content) > 200:
            return True
    return False


def safe_page_goto(
    page,
    url: str,
    *,
    deadline_ts: float,
    referer: Optional[str] = None,
    max_attempts: Optional[int] = None,
    label: str = "",
) -> bool:
    """Navigate with retries, progressive wait_until, and deadline-aware timeouts.

    Returns True when the page is usable (even if a later wait_until timed out
    after partial load). Returns False when all attempts fail before deadline.
    """
    if not url or timeout_left_ms(deadline_ts) <= 0:
        return False
    try:
        attempts = int(os.getenv("LANZOU_NAV_RETRIES", "3")) if max_attempts is None else int(max_attempts)
    except (TypeError, ValueError):
        attempts = 3
    attempts = max(1, min(attempts, 5))

    # commit fires earlier than domcontentloaded; use it first under pressure.
    wait_strategies = ("commit", "domcontentloaded")
    last_err: Optional[BaseException] = None
    tag = f" {label}" if label else ""

    for attempt in range(1, attempts + 1):
        left = timeout_left_ms(deadline_ts)
        if left <= 0:
            break
        wait_until = wait_strategies[(attempt - 1) % len(wait_strategies)]
        # Leave a little budget for post-nav work; never below 5s when possible.
        nav_timeout = navigation_timeout_ms(deadline_ts)
        if nav_timeout < 1000:
            break
        kwargs = {
            "wait_until": wait_until,
            "timeout": nav_timeout,
        }
        if referer:
            kwargs["referer"] = referer
        try:
            page.goto(url, **kwargs)
            return True
        except Exception as e:
            last_err = e
            if is_target_closed_error(e):
                raise
            usable = page_looks_loaded(page)
            if usable:
                log(
                    f"[WARN] page.goto partial load accepted{tag} "
                    f"attempt={attempt}/{attempts} wait_until={wait_until}: {e}"
                )
                return True
            if not is_navigation_timeout_error(e) and "net::" not in str(e).lower():
                # Non-timeout hard error: one quick retry only if budget remains.
                if attempt >= attempts:
                    break
            log(
                f"[WARN] page.goto failed{tag} attempt={attempt}/{attempts} "
                f"wait_until={wait_until} timeout_ms={nav_timeout}: {e}"
            )
            # Brief backoff before retry; keep it small vs. remaining deadline.
            backoff = min(1500 * attempt, max(0, timeout_left_ms(deadline_ts) // 4), 4000)
            if backoff > 0:
                try:
                    page.wait_for_timeout(backoff)
                except Exception:
                    time.sleep(backoff / 1000.0)

    if last_err is not None:
        log(f"[WARN] page.goto exhausted retries{tag}: {last_err}")
    return False


def item_download_retries() -> int:
    try:
        value = int(os.getenv("LANZOU_ITEM_RETRIES", "2"))
    except (TypeError, ValueError):
        value = 2
    return max(1, min(value, 4))


def is_retryable_item_status(status: Optional[str]) -> bool:
    if not status:
        return True
    value = status.lower()
    if value in {"no_download", "nav_timeout", "timeout", "target_closed"}:
        return True
    return "timeout" in value or "goto" in value or value.startswith("exception:")


def normalize_candidate_url(raw: str, base_url: str) -> Optional[str]:
    if not raw:
        return None
    url = html_lib.unescape(str(raw).strip().strip("'\""))
    if not url:
        return None
    url = url.replace("\\/", "/").replace("\\u002F", "/")
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/"):
        url = urljoin(base_url, url)
    elif url.startswith("?"):
        url = f"{base_url.rstrip('/')}{url}"
    if not (url.startswith("http://") or url.startswith("https://")):
        return None
    return url


def is_lanrar_file_page(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return "lanrar.com" in host and path.startswith("/file/")


def is_download_candidate_url(url: str) -> bool:
    url_l = url.lower()
    blocked_suffix = (
        ".css",
        ".js",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".map",
    )
    if any(url_l.endswith(x) for x in blocked_suffix):
        return False
    signals = (
        ".zip",
        ".7z",
        ".rar",
        "/file/?",
        "developer-oss.lanrar.com/file",
        "/down",
        "/download",
        "/fn?",
        "token=",
        "sign=",
    )
    return any(s in url_l for s in signals)


def extract_candidate_urls_from_text(text: str, base_url: str) -> List[str]:
    urls: List[str] = []
    if not text:
        return urls
    patterns = [
        r"https?://[^\s\"'<>\\]+",
        r"(?i)(?:url|link|downloadurl|downurl)\s*[:=]\s*[\"']([^\"']+)[\"']",
        r"(?i)href\s*=\s*[\"']([^\"']+)[\"']",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            raw = m.group(1) if m.groups() else m.group(0)
            url = normalize_candidate_url(raw, base_url)
            if url and is_download_candidate_url(url):
                urls.append(url)
    return urls


def collect_page_download_candidates(page) -> List[str]:
    candidates: List[str] = []
    try:
        scopes = all_scopes(page)
    except Exception:
        return candidates
    for scope in scopes:
        base = page.url
        try:
            if hasattr(scope, "url") and scope.url:
                base = scope.url
        except Exception:
            pass
        try:
            txt = scope.content()
            candidates.extend(extract_candidate_urls_from_text(txt, base))
        except Exception:
            pass
        try:
            anchors = scope.locator("a[href]")
            count = min(anchors.count(), 200)
            for i in range(count):
                href = anchors.nth(i).get_attribute("href")
                url = normalize_candidate_url(href, base)
                if url and is_download_candidate_url(url):
                    candidates.append(url)
        except Exception:
            pass
    uniq: List[str] = []
    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        uniq.append(url)
    return uniq


def infer_ext_from_response(resp, fallback: str = ".zip") -> str:
    cd = (resp.headers.get("content-disposition", "") or "").strip()
    if cd:
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", cd, re.I)
        if m:
            filename = unquote(m.group(1).strip().strip('"'))
            ext = os.path.splitext(filename)[1]
            if ext:
                return ext
    path = unquote(urlparse(resp.url).path)
    ext = os.path.splitext(path)[1]
    if ext and len(ext) <= 8:
        return ext
    ctype = (resp.headers.get("content-type", "") or "").lower()
    if "7z" in ctype:
        return ".7z"
    if "rar" in ctype:
        return ".rar"
    if "zip" in ctype:
        return ".zip"
    if "epub" in ctype:
        return ".epub"
    return fallback


def infer_filename_from_headers(headers: Dict[str, str], url: str, fallback_title: str, fallback_ext: str = ".zip") -> str:
    cd = (headers.get("content-disposition", "") or "").strip()
    if cd:
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", cd, re.I)
        if m:
            filename = safe_filename(unquote(m.group(1).strip().strip('"')))
            if filename:
                return filename
    path = unquote(urlparse(url).path)
    name = safe_filename(os.path.basename(path))
    if name and os.path.splitext(name)[1]:
        return name
    return safe_filename(f"{fallback_title}{fallback_ext}")


def infer_filename_from_response(resp, fallback_title: str, fallback_ext: str = ".zip") -> str:
    return infer_filename_from_headers(
        resp.headers,
        resp.url,
        fallback_title=fallback_title,
        fallback_ext=infer_ext_from_response(resp, fallback=fallback_ext),
    )


def item_priority(item: Dict[str, str]) -> Tuple[int, int, str]:
    text = item.get("text", "")
    text_l = text.lower()
    if item.get("kind") == "bundle":
        ext_order = {".zip": 0, ".7z": 1, ".rar": 2}
        ext_rank = 9
        for ext, rank in ext_order.items():
            if text_l.endswith(ext):
                ext_rank = rank
                break
        return (0, ext_rank, text)
    return (1, 0, text)


def is_zht_item(item: Dict[str, str]) -> bool:
    if is_zht_name(item.get("text", "")):
        return True
    href = item.get("href", "")
    if is_zht_name(os.path.basename(unquote(urlparse(href).path))):
        return True
    return False


def strip_html_tags(text: str) -> str:
    value = html_lib.unescape(text or "")
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_js_var(text: str, name: str) -> Optional[str]:
    for pattern in [
        rf"var\s+{re.escape(name)}\s*=\s*'([^']*)'",
        rf'var\s+{re.escape(name)}\s*=\s*"([^"]*)"',
        rf"var\s+{re.escape(name)}\s*=\s*([0-9]+)",
    ]:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def resolve_js_expr(text: str, expr: str) -> Optional[str]:
    value = (expr or "").strip().rstrip(",")
    if not value:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    if re.fullmatch(r"[0-9]+", value):
        return value
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        return extract_js_var(text, value)
    return None


def extract_js_field_exprs(text: str, key: str) -> List[str]:
    patterns = [
        rf"['\"]{re.escape(key)}['\"]\s*:\s*([^,\n}}]+)",
        rf"\b{re.escape(key)}\b\s*:\s*([^,\n}}]+)",
    ]
    exprs: List[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            expr = (match.group(1) or "").strip()
            if expr:
                exprs.append(expr)
    return exprs


def extract_js_field_value(text: str, key: str, prefer_last: bool = False) -> Optional[str]:
    exprs = extract_js_field_exprs(text, key)
    if prefer_last:
        exprs = list(reversed(exprs))
    for expr in exprs:
        value = resolve_js_expr(text, expr)
        if value is not None:
            return value
    return None


def parse_iframe_src(text: str, base_url: str) -> Optional[str]:
    match = re.search(r"<iframe[^>]+src=['\"]([^'\"]+)['\"]", text, re.I)
    if not match:
        return None
    return urljoin(base_url, match.group(1))


def extract_ajaxm_file_id(text: str) -> Optional[str]:
    matches = re.findall(r"/ajaxm\.php\?file=(\d+)", text)
    if not matches:
        return None
    for file_id in reversed(matches):
        if file_id != "1":
            return file_id
    return matches[-1]


def resolve_lanrar_ajax_url(url: str, referer: str, timeout_ms: int) -> Optional[str]:
    if timeout_ms <= 0:
        return None
    deadline_ts = time.monotonic() + (timeout_ms / 1000.0)
    request_timeout_ms = remaining_timeout_ms(deadline_ts, 30000)
    if request_timeout_ms <= 0:
        return None
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": referer or url,
    }
    try:
        resp = SESSION.get(
            url,
            headers=headers,
            timeout=bounded_timeout_s(request_timeout_ms, min_s=1.0, max_s=30.0),
        )
    except Exception:
        return None
    try:
        if resp.status_code >= 400:
            return None
        text = resp.text
        response_url = resp.url
    finally:
        try:
            resp.close()
        except Exception:
            pass

    # toolsdown 页里可能有多组 file/sign；只取真正下载按钮 down_r(el) 里的那组。
    match = re.search(
        r"function\s+down_r\s*\(\s*el\s*\)\s*\{.*?data\s*:\s*\{\s*'file':'([^']+)'\s*,\s*'el':el\s*,\s*'sign':'([^']+)'",
        text,
        re.S,
    )
    if not match:
        return None

    ajax_url = urljoin(url, "ajax.php")
    payload = {
        "file": match.group(1),
        "sign": match.group(2),
        "el": "2",  # 优先普通下载
    }
    ajax_headers = {
        "User-Agent": headers["User-Agent"],
        "Referer": response_url,
        "X-Requested-With": "XMLHttpRequest",
    }
    request_timeout_ms = remaining_timeout_ms(deadline_ts, 30000)
    if request_timeout_ms <= 0:
        return None
    try:
        ajax_resp = SESSION.post(
            ajax_url,
            headers=ajax_headers,
            data=payload,
            timeout=bounded_timeout_s(request_timeout_ms, min_s=1.0, max_s=30.0),
        )
    except Exception:
        return None
    try:
        if ajax_resp.status_code >= 400:
            return None
        data = ajax_resp.json()
    except Exception:
        return None
    finally:
        try:
            ajax_resp.close()
        except Exception:
            pass
    if str(data.get("zt")) != "1":
        return None
    final_url = data.get("url")
    if not isinstance(final_url, str) or not final_url.strip():
        return None
    final_url = final_url.strip()
    if not (final_url.startswith("http://") or final_url.startswith("https://")):
        return None
    return final_url


def max_download_bytes() -> int:
    default = 2 * 1024 * 1024 * 1024
    try:
        configured = int(os.getenv("LANZOU_MAX_DOWNLOAD_BYTES", str(default)))
    except (TypeError, ValueError):
        return default
    return configured if configured > 0 else default


def sync_browser_cookies_to_session(context, url: str) -> None:
    try:
        cookies = context.cookies([url])
    except Exception:
        return
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        kwargs = {"path": cookie.get("path") or "/"}
        if cookie.get("domain"):
            kwargs["domain"] = cookie["domain"]
        SESSION.cookies.set(name, value, **kwargs)


def download_direct_file(url: str, download_dir: Path, title: str, referer: str, timeout_ms: int) -> Optional[Path]:
    if timeout_ms <= 0:
        return None
    deadline_ts = time.monotonic() + (timeout_ms / 1000.0)
    request_timeout_s = bounded_timeout_s(timeout_ms, min_s=1.0, max_s=30.0)
    try:
        resp = SESSION.get(
            url,
            headers={"User-Agent": random.choice(USER_AGENTS), "Referer": referer or url},
            timeout=(min(request_timeout_s, 10.0), min(request_timeout_s, 15.0)),
            allow_redirects=True,
            stream=True,
        )
    except Exception:
        return None
    partial: Optional[Path] = None
    try:
        if resp.status_code >= 400:
            return None
        byte_limit = max_download_bytes()
        try:
            declared_size = int(resp.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            declared_size = 0
        if declared_size > byte_limit:
            log(
                f"[WARN] 蓝奏文件超过下载上限，拒绝缓存: "
                f"size={declared_size} limit={byte_limit} title={title}"
            )
            return None
        filename = infer_filename_from_response(resp, fallback_title=title, fallback_ext=".zip")
        target = unique_path(download_dir, filename)
        partial = unique_path(download_dir, f"{filename}.part")
        size = 0
        with partial.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                if timeout_left_ms(deadline_ts) <= 0:
                    return None
                if size + len(chunk) > byte_limit:
                    log(
                        f"[WARN] 蓝奏流式下载超过上限，已中止: "
                        f"limit={byte_limit} title={title}"
                    )
                    return None
                f.write(chunk)
                size += len(chunk)
        if size > 0:
            partial.replace(target)
            partial = None
            return target
        return None
    finally:
        safe_unlink(partial)
        try:
            resp.close()
        except Exception:
            pass


def download_from_candidate_urls(candidates: Iterable[str], download_dir: Path, title: str, referer: str, timeout_ms: int) -> Optional[Path]:
    if timeout_ms <= 0:
        return None
    queue: List[Tuple[str, str]] = []
    for candidate in list(candidates)[:12]:
        queue.append((candidate, referer))
    seen = set()
    deadline_ts = time.monotonic() + (timeout_ms / 1000.0)
    max_attempts = 16
    max_queue = 24
    attempts = 0
    while queue and attempts < max_attempts:
        left_ms = timeout_left_ms(deadline_ts)
        if left_ms <= 0:
            break
        url, current_referer = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        attempts += 1
        request_timeout_ms = min(left_ms, 45000)
        timeout_s = bounded_timeout_s(request_timeout_ms, min_s=1.0, max_s=45.0)
        if is_lanrar_file_page(url):
            resolved = resolve_lanrar_ajax_url(
                url, referer=current_referer or url, timeout_ms=request_timeout_ms
            )
            if resolved and resolved not in seen and len(queue) < max_queue:
                queue.insert(0, (resolved, url))
            continue
        try:
            resp = SESSION.get(
                url,
                headers={"User-Agent": random.choice(USER_AGENTS), "Referer": current_referer or referer or url},
                timeout=timeout_s,
                allow_redirects=True,
                stream=True,
            )
        except Exception:
            continue
        try:
            if resp.status_code >= 400:
                continue
            ctype = (resp.headers.get("content-type", "") or "").lower()
            is_html_like = ("text/html" in ctype) or (
                "application/json" in ctype and "attachment" not in (resp.headers.get("content-disposition", "") or "").lower()
            )
            if is_html_like:
                if is_lanrar_file_page(resp.url):
                    resolved = resolve_lanrar_ajax_url(
                        resp.url, referer=current_referer or resp.url, timeout_ms=request_timeout_ms
                    )
                    if resolved and resolved not in seen and len(queue) < max_queue:
                        queue.insert(0, (resolved, resp.url))
                if attempts <= 8 and len(queue) < max_queue:
                    body = resp.text[:120000]
                    nested = extract_candidate_urls_from_text(body, resp.url)
                    for nested_url in nested[:6]:
                        if nested_url not in seen and len(queue) < max_queue:
                            queue.append((nested_url, resp.url))
                continue

            filename = infer_filename_from_response(resp, fallback_title=title, fallback_ext=".zip")
            target = unique_path(download_dir, filename)
            size = 0
            with target.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    if timeout_left_ms(deadline_ts) <= 0:
                        break
                    f.write(chunk)
                    size += len(chunk)
            if size > 0 and timeout_left_ms(deadline_ts) > 0:
                return target
            try:
                target.unlink()
            except Exception:
                pass
        finally:
            try:
                resp.close()
            except Exception:
                pass
    return None


def fetch_share_items_via_ajax(page_url: str, pwd: str, timeout_ms: int) -> List[Dict[str, str]]:
    if timeout_ms <= 0:
        return []
    timeout_s = bounded_timeout_s(timeout_ms, min_s=1.0, max_s=30.0)
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": page_url,
    }
    try:
        resp = SESSION.get(page_url, headers=headers, timeout=timeout_s)
    except Exception:
        return []
    try:
        if resp.status_code >= 400:
            return []
        text = resp.text
    finally:
        try:
            resp.close()
        except Exception:
            pass

    ajax_matches = re.findall(r"url\s*:\s*'([^']*filemoreajax\.php\?file=\d+[^']*)'", text)
    ajax_rel_match = ajax_matches[-1] if ajax_matches else None
    fid_match = re.search(r"'fid'\s*:\s*'?(\d+)'?", text)
    uid_match = re.search(r"'uid'\s*:\s*'([^']+)'", text)
    page_match = re.search(r"pgs\s*=\s*(\d+)", text)
    ls_match = re.search(r"'ls'\s*:\s*'?(\d+)'?", text)
    time_value = extract_js_field_value(text, "t")
    key_value = extract_js_field_value(text, "k")
    if not (ajax_rel_match and fid_match and uid_match and time_value and key_value):
        return []

    ajax_url = urljoin(page_url, ajax_rel_match)
    payload = {
        "lx": "2",
        "fid": fid_match.group(1),
        "uid": uid_match.group(1),
        "pg": (page_match.group(1) if page_match else "1"),
        "rep": "0",
        "t": time_value,
        "k": key_value,
        "up": "1",
        "ls": (ls_match.group(1) if ls_match else "1"),
        "pwd": pwd or "",
    }
    ajax_headers = {
        "User-Agent": headers["User-Agent"],
        "Referer": page_url,
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        ajax_resp = SESSION.post(ajax_url, headers=ajax_headers, data=payload, timeout=timeout_s)
    except Exception:
        return []
    try:
        if ajax_resp.status_code >= 400:
            return []
        data = ajax_resp.json()
    except Exception:
        return []
    finally:
        try:
            ajax_resp.close()
        except Exception:
            pass
    if str(data.get("zt")) != "1":
        return []

    items: List[Dict[str, str]] = []
    seen = set()
    for raw in data.get("text") or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("t") or "") == "1":
            continue
        name = strip_html_tags(str(raw.get("name_all") or raw.get("name") or "")).strip()
        item_id = str(raw.get("id") or "").strip()
        if not name or not item_id:
            continue
        href = item_id if item_id.startswith("http") else urljoin(page_url, f"/{item_id.lstrip('/')}")
        key = (name, href)
        if key in seen:
            continue
        seen.add(key)
        kind = "other"
        name_l = name.lower()
        if "合集" in name and any(ext in name_l for ext in [".zip", ".7z", ".rar"]):
            kind = "bundle"
        elif name_l.endswith(".epub"):
            kind = "epub"
        items.append({"text": name, "href": href, "kind": kind})
    return items


def fill_lanzou_password(page, pwd: str) -> None:
    if not pwd:
        return
    for _ in range(3):
        pwd_loc = first_locator_any_scope(
            page,
            [
                "#pwd",
                "input[name='pwd']",
                "input[type='password']",
                "input[id*='pwd']",
                "input[placeholder*='密码']",
                "input:not([type='hidden'])",
            ],
        )
        if pwd_loc is not None:
            try:
                pwd_loc.fill(pwd)
            except Exception:
                pass
            submit_loc = first_locator_any_scope(
                page,
                [
                    "#sub",
                    "button:has-text('确定')",
                    "button:has-text('提取')",
                    "input[type='submit']",
                    "input[type='button']",
                    "text=确定",
                    "text=提取",
                ],
            )
            if submit_loc is not None:
                try:
                    submit_loc.click(force=True)
                except Exception:
                    try:
                        pwd_loc.press("Enter")
                    except Exception:
                        pass
            else:
                try:
                    pwd_loc.press("Enter")
                except Exception:
                    pass
            page.wait_for_timeout(1500)
            return
        page.wait_for_timeout(800)


def click_and_follow(page, node, timeout_ms: int):
    context = page.context
    known = set(context.pages)
    load_timeout = max(3000, min(timeout_ms, 15000))
    try:
        with context.expect_page(timeout=min(timeout_ms, 5000)) as new_page_info:
            node.click(force=True)
        new_page = new_page_info.value
        new_page.wait_for_load_state("domcontentloaded", timeout=load_timeout)
        return new_page
    except Exception:
        try:
            node.click(force=True)
        except Exception:
            return page
        page.wait_for_timeout(800)
        for candidate in context.pages:
            if candidate not in known:
                candidate.wait_for_load_state("domcontentloaded", timeout=load_timeout)
                return candidate
        return page


def try_click_download(page, node, timeout_ms: int):
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        with page.expect_download(timeout=timeout_ms) as dl_info:
            node.click(force=True)
        return dl_info.value
    except PlaywrightTimeoutError:
        return None
    except Exception:
        return None


def wait_for_async_download(page, timeout_ms: int):
    if timeout_ms <= 1:
        return None
    try:
        return page.wait_for_event("download", timeout=timeout_ms)
    except Exception:
        return None


def select_bundle_file_page(page, timeout_ms: int):
    quick_bundle = first_locator_any_scope(
        page,
        [
            "a:has-text('合集.zip')",
            "a:has-text('合集.7z')",
            "a:has-text('合集.rar')",
            "a:has-text('合集')",
        ],
    )
    if quick_bundle is not None:
        return click_and_follow(page, quick_bundle, timeout_ms)

    for scope in all_scopes(page):
        anchors = scope.locator("a")
        count = min(anchors.count(), 120)
        bundle_candidates = []
        for i in range(count):
            node = anchors.nth(i)
            try:
                txt = node.inner_text(timeout=250).strip()
            except Exception:
                continue
            txt_l = txt.lower()
            if "合集" not in txt:
                continue
            if not any(ext in txt_l for ext in [".zip", ".7z", ".rar"]):
                continue
            bundle_candidates.append(node)
        if bundle_candidates:
            return click_and_follow(page, bundle_candidates[0], timeout_ms)
    return None


def collect_share_items(page) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    seen = set()
    for scope in all_scopes(page):
        base = page.url
        try:
            if hasattr(scope, "url") and scope.url:
                base = scope.url
        except Exception:
            pass
        anchors = scope.locator("a")
        count = min(anchors.count(), 200)
        for i in range(count):
            node = anchors.nth(i)
            try:
                txt = node.inner_text(timeout=250).strip()
            except Exception:
                continue
            try:
                href = node.get_attribute("href")
            except Exception:
                href = None
            if not txt or not href:
                continue
            href_abs = normalize_candidate_url(href, base) or urljoin(base, href)
            key = (txt, href_abs)
            if key in seen:
                continue
            seen.add(key)

            item: Dict[str, str] = {
                "text": txt,
                "href": href_abs,
                "kind": "other",
            }
            txt_l = txt.lower()
            if "合集" in txt and any(ext in txt_l for ext in [".zip", ".7z", ".rar"]):
                item["kind"] = "bundle"
            elif txt_l.endswith(".epub"):
                item["kind"] = "epub"
            items.append(item)
    return items


def pick_share_items(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """If any 合集 exists, return ALL 合集 (skip single-volume epubs entirely).

    Multi-part shares like 实力至上 have 合集1.zip / 合集2.zip / 合集3.zip — all needed.
    """
    bundle_items = [
        item
        for item in items
        if item["kind"] == "bundle" and not is_zht_item(item)
    ]
    if bundle_items:
        # Sort zip first; keep every distinct 合集 (cap avoids runaway pages)
        max_bundles = max(1, int(os.getenv("LANZOU_MAX_BUNDLES", "20")))
        return sorted(bundle_items, key=item_priority)[:max_bundles]
    epub_items = [
        item
        for item in items
        if item["kind"] == "epub" and not is_zht_item(item)
    ]
    return sorted(epub_items, key=item_priority)


def open_share_item_page(context, item: Dict[str, str], timeout_ms: int):
    page = context.new_page()
    deadline_ts = time.monotonic() + (max(int(timeout_ms), 1) / 1000.0)
    if not safe_page_goto(
        page,
        item["href"],
        deadline_ts=deadline_ts,
        label=item.get("text") or "",
    ):
        raise RuntimeError(f"page.goto failed: {item.get('href')}")
    return page


def resolve_browser_download_link(page, deadline_ts: float) -> Optional[str]:
    if timeout_left_ms(deadline_ts) <= 1:
        return None
    try:
        content = page.content()
    except Exception:
        content = ""
    has_verify_flow = ("function down_r" in content) or ("验证并下载" in content)
    if not has_verify_flow:
        return None

    try:
        page.evaluate("down_r(2)")
    except Exception:
        verify_loc = first_locator_any_scope(
            page,
            [
                "#sub",
                "#go",
                "text=验证并下载",
                "button:has-text('验证并下载')",
                "a:has-text('验证并下载')",
            ],
        )
        if verify_loc is None:
            return None
        try:
            verify_loc.click(force=True)
        except Exception:
            return None

    end_ts = time.monotonic() + min(timeout_left_ms(deadline_ts), 10000) / 1000.0
    while time.monotonic() < end_ts:
        for selector in [
            "#go a[href]",
            "a[href]:has-text('立即下载')",
            "a[href]:has-text('即刻下载')",
            "a[href*='webgetstore.com']",
        ]:
            loc = first_locator_any_scope(page, [selector])
            if loc is None:
                continue
            try:
                href = loc.get_attribute("href")
            except Exception:
                href = None
            href = normalize_candidate_url(href, page.url) if href else None
            if href and href.startswith(("http://", "https://")) and "SignError" not in href:
                return href
        page.wait_for_timeout(500)
    return None


def open_normal_download_page(page, deadline_ts: float):
    left = timeout_left_ms(deadline_ts)
    if left <= 1:
        return page, None
    normal_loc = first_locator_any_scope(
        page,
        [
            "a:has-text('普通下载')",
            "button:has-text('普通下载')",
            "input[value='普通下载']",
            "a#tourl",
            "text=普通下载",
        ],
    )
    if normal_loc is None:
        return page, None
    direct_download = try_click_download(page, normal_loc, timeout_ms=min(timeout_left_ms(deadline_ts), 8000))
    if direct_download is not None:
        return page, direct_download
    left = timeout_left_ms(deadline_ts)
    if left <= 1:
        return page, None
    return click_and_follow(page, normal_loc, min(left, 8000)), None


def resolve_verify_and_download(page, deadline_ts: float, depth: int = 0, verify_clicked: bool = False):
    if depth > 2 or timeout_left_ms(deadline_ts) <= 1:
        return None
    if not verify_clicked:
        verify_loc = first_locator_any_scope(
            page,
            [
                "button:has-text('验证并下载')",
                "a:has-text('验证并下载')",
                "input[value='验证并下载']",
                "text=验证并下载",
            ],
        )
        if verify_loc is not None:
            try:
                verify_loc.click(force=True)
            except Exception:
                pass
            wait_ms = remaining_timeout_ms(deadline_ts, 2200)
            if wait_ms <= 0:
                return None
            page.wait_for_timeout(wait_ms)
            async_dl = wait_for_async_download(page, min(timeout_left_ms(deadline_ts), 9000))
            if async_dl is not None:
                return async_dl
            verify_clicked = True

    for _ in range(4):
        if timeout_left_ms(deadline_ts) <= 1:
            return None
        for selector in [
            "button:has-text('即刻下载')",
            "button:has-text('立即下载')",
            "a:has-text('即刻下载')",
            "a:has-text('立即下载')",
            "button:has-text('普通下载')",
            "a:has-text('普通下载')",
            "a#tourl",
            "button:has-text('下载')",
            "a:has-text('下载')",
            "a[href*='down']",
            "a[href*='file']",
            "text=即刻下载",
            "text=立即下载",
        ]:
            if timeout_left_ms(deadline_ts) <= 1:
                return None
            for scope in all_scopes(page):
                loc = scope.locator(selector)
                if loc.count() == 0:
                    continue
                node = loc.first
                try:
                    if hasattr(node, "is_enabled") and (not node.is_enabled()):
                        continue
                except Exception:
                    pass
                click_timeout = min(timeout_left_ms(deadline_ts), 6000)
                download = try_click_download(page, node, timeout_ms=click_timeout)
                if download is not None:
                    return download
                try:
                    nxt = click_and_follow(page, node, timeout_ms=min(timeout_left_ms(deadline_ts), 6000))
                    if nxt != page:
                        nested = resolve_verify_and_download(nxt, deadline_ts, depth + 1, verify_clicked)
                        if nested is not None:
                            return nested
                    async_dl = wait_for_async_download(page, min(timeout_left_ms(deadline_ts), 3500))
                    if async_dl is not None:
                        return async_dl
                except Exception:
                    continue
        sleep_ms = min(1200, timeout_left_ms(deadline_ts))
        if sleep_ms <= 1:
            return None
        page.wait_for_timeout(sleep_ms)
    return None


def resolve_item_candidate_url(item_url: str, referer: str, timeout_ms: int) -> Tuple[Optional[str], Optional[str], str]:
    if timeout_ms <= 0:
        return None, None, "timeout"
    deadline_ts = time.monotonic() + (timeout_ms / 1000.0)
    item_headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": referer or item_url,
    }
    request_timeout_ms = remaining_timeout_ms(deadline_ts, 30000)
    if request_timeout_ms <= 0:
        return None, None, "timeout"
    try:
        item_resp = SESSION.get(
            item_url,
            headers=item_headers,
            timeout=bounded_timeout_s(request_timeout_ms, min_s=1.0, max_s=30.0),
        )
    except Exception as e:
        return None, None, f"item_request_error:{e}"
    try:
        if item_resp.status_code >= 400:
            return None, None, f"item_status:{item_resp.status_code}"
        item_page_url = item_resp.url
        iframe_url = parse_iframe_src(item_resp.text, item_page_url)
        if not iframe_url:
            return None, None, "no_iframe"
    finally:
        try:
            item_resp.close()
        except Exception:
            pass

    iframe_headers = {
        "User-Agent": item_headers["User-Agent"],
        "Referer": item_page_url,
    }
    request_timeout_ms = remaining_timeout_ms(deadline_ts, 30000)
    if request_timeout_ms <= 0:
        return None, None, "timeout"
    try:
        iframe_resp = SESSION.get(
            iframe_url,
            headers=iframe_headers,
            timeout=bounded_timeout_s(request_timeout_ms, min_s=1.0, max_s=30.0),
        )
    except Exception as e:
        return None, None, f"iframe_request_error:{e}"
    try:
        if iframe_resp.status_code >= 400:
            return None, None, f"iframe_status:{iframe_resp.status_code}"
        iframe_text = iframe_resp.text
        ajax_file_id = extract_ajaxm_file_id(iframe_text)
        ajaxdata = extract_js_var(iframe_text, "ajaxdata")
        wp_sign = extract_js_var(iframe_text, "wp_sign")
        websign = extract_js_field_value(iframe_text, "websign")
        kdns = extract_js_var(iframe_text, "kdns") or "1"
        if not (ajax_file_id and ajaxdata and wp_sign):
            return None, None, "ajaxm_params_missing"
        ajax_url = urljoin(iframe_resp.url, f"/ajaxm.php?file={ajax_file_id}")
    finally:
        try:
            iframe_resp.close()
        except Exception:
            pass

    payload = {
        "action": "downprocess",
        "websignkey": ajaxdata,
        "signs": ajaxdata,
        "sign": wp_sign,
        "websign": websign or "",
        "kd": kdns,
        "ves": 1,
    }
    ajax_headers = {
        "User-Agent": item_headers["User-Agent"],
        "Referer": iframe_url,
        "X-Requested-With": "XMLHttpRequest",
    }
    request_timeout_ms = remaining_timeout_ms(deadline_ts, 30000)
    if request_timeout_ms <= 0:
        return None, None, "timeout"
    try:
        ajax_resp = SESSION.post(
            ajax_url,
            headers=ajax_headers,
            data=payload,
            timeout=bounded_timeout_s(request_timeout_ms, min_s=1.0, max_s=30.0),
        )
    except Exception as e:
        return None, None, f"ajaxm_request_error:{e}"
    try:
        if ajax_resp.status_code >= 400:
            return None, None, f"ajaxm_status:{ajax_resp.status_code}"
        data = ajax_resp.json()
    except Exception as e:
        return None, None, f"ajaxm_json_error:{e}"
    finally:
        try:
            ajax_resp.close()
        except Exception:
            pass

    if str(data.get("zt")) != "1":
        return None, None, f"ajaxm_zt:{data.get('zt')}"
    dom = str(data.get("dom") or "").strip().rstrip("/")
    path = str(data.get("url") or "").strip()
    if not (dom and path):
        return None, None, "ajaxm_missing_url"
    candidate_url = f"{dom}/file/{path.lstrip('/')}"
    return candidate_url, iframe_url, "ok"


def download_item_via_http(context, item: Dict[str, str], share_url: str, download_dir: Path, title: str, timeout_ms: int):
    if timeout_ms <= 0:
        return None, "timeout"
    deadline_ts = time.monotonic() + (timeout_ms / 1000.0)
    candidate_url, candidate_referer, status = resolve_item_candidate_url(
        item["href"],
        referer=share_url,
        timeout_ms=remaining_timeout_ms(deadline_ts, timeout_ms),
    )
    if not candidate_url:
        return None, status

    lanrar_candidates = [candidate_url]
    if "&toolsdown" not in candidate_url.lower():
        lanrar_candidates.append(candidate_url + "&toolsdown")

    for lanrar_url in lanrar_candidates:
        operation_timeout_ms = remaining_timeout_ms(deadline_ts, 30000)
        if operation_timeout_ms <= 0:
            return None, "timeout"
        final_url = resolve_lanrar_ajax_url(
            lanrar_url,
            referer=candidate_referer or share_url,
            timeout_ms=operation_timeout_ms,
        )
        if not final_url and timeout_left_ms(deadline_ts) > 0:
            verify_page = None
            try:
                verify_page = context.new_page()
                if safe_page_goto(
                    verify_page,
                    lanrar_url,
                    deadline_ts=deadline_ts,
                    referer=candidate_referer or share_url,
                    label="lanrar-verify",
                ):
                    wait_ms = remaining_timeout_ms(deadline_ts, 1500)
                    if wait_ms > 0:
                        verify_page.wait_for_timeout(wait_ms)
                    final_url = resolve_browser_download_link(verify_page, deadline_ts)
            except Exception:
                final_url = None
            finally:
                if verify_page is not None:
                    try:
                        verify_page.close()
                    except Exception:
                        pass
        if not final_url:
            continue
        operation_timeout_ms = remaining_timeout_ms(deadline_ts, 90000)
        if operation_timeout_ms <= 0:
            return None, "timeout"
        sync_browser_cookies_to_session(context, final_url)
        downloaded = download_direct_file(
            final_url,
            download_dir=download_dir,
            title=title,
            referer=lanrar_url,
            timeout_ms=operation_timeout_ms,
        )
        if downloaded:
            return downloaded, "ok"
    return None, "timeout" if timeout_left_ms(deadline_ts) <= 0 else "no_download"


def download_share_item(page, item: Dict[str, str], share_url: str, download_dir: Path, title: str, deadline_ts: float, timeout_ms: int):
    item_title = item["text"] if item["kind"] == "epub" else title
    remaining = min(timeout_left_ms(deadline_ts), timeout_ms)
    # Keep budget for the browser fallback; HTTP often fails quickly on flaky shares.
    reserve_browser_ms = min(45000, remaining // 2) if remaining > 25000 else 0
    http_budget = remaining - reserve_browser_ms
    direct_out, direct_status = download_item_via_http(
        page.context,
        item=item,
        share_url=share_url,
        download_dir=download_dir,
        title=item_title,
        timeout_ms=http_budget,
    )
    if direct_out:
        return direct_out, "ok"
    if timeout_left_ms(deadline_ts) <= 0:
        return None, "timeout"

    last_status = direct_status if direct_status and direct_status != "no_download" else "no_download"
    work_page = page
    created_page = False
    try:
        if not safe_page_goto(
            work_page,
            item["href"],
            deadline_ts=deadline_ts,
            referer=share_url,
            label=item_title,
        ):
            if timeout_left_ms(deadline_ts) <= 0:
                return None, "timeout"
            try:
                work_page = page.context.new_page()
                created_page = True
            except Exception:
                return None, last_status or "nav_timeout"
            if not safe_page_goto(
                work_page,
                item["href"],
                deadline_ts=deadline_ts,
                referer=share_url,
                label=f"{item_title} (fresh page)",
            ):
                return None, "nav_timeout"
        wait_ms = remaining_timeout_ms(deadline_ts, 2000)
        if wait_ms > 0:
            work_page.wait_for_timeout(wait_ms)
        if timeout_left_ms(deadline_ts) <= 0:
            return None, "timeout"
        candidate_urls = collect_page_download_candidates(work_page)
        try:
            candidate_urls.insert(0, work_page.url)
        except Exception:
            pass
        browser_candidates = []
        for browser_candidate in [work_page.url] + candidate_urls:
            if not is_lanrar_file_page(browser_candidate):
                continue
            if "toolsdown" not in browser_candidate.lower():
                continue
            if browser_candidate in browser_candidates:
                continue
            browser_candidates.append(browser_candidate)
        for browser_candidate in browser_candidates:
            if timeout_left_ms(deadline_ts) <= 0:
                return None, "timeout"
            verify_page = None
            try:
                verify_page = page.context.new_page()
                if not safe_page_goto(
                    verify_page,
                    browser_candidate,
                    deadline_ts=deadline_ts,
                    referer=work_page.url,
                    label="item-verify",
                ):
                    continue
                wait_ms = remaining_timeout_ms(deadline_ts, 1500)
                if wait_ms > 0:
                    verify_page.wait_for_timeout(wait_ms)
                browser_final_url = resolve_browser_download_link(verify_page, deadline_ts)
                if not browser_final_url:
                    continue
                operation_timeout_ms = remaining_timeout_ms(deadline_ts, timeout_ms)
                if operation_timeout_ms <= 0:
                    return None, "timeout"
                sync_browser_cookies_to_session(page.context, browser_final_url)
                direct_out = download_direct_file(
                    browser_final_url,
                    download_dir=download_dir,
                    title=item_title,
                    referer=verify_page.url,
                    timeout_ms=operation_timeout_ms,
                )
                if direct_out:
                    return direct_out, "ok"
            finally:
                if verify_page is not None:
                    try:
                        verify_page.close()
                    except Exception:
                        pass
        operation_timeout_ms = remaining_timeout_ms(deadline_ts, timeout_ms)
        if operation_timeout_ms <= 0:
            return None, "timeout"
        direct_candidates = [candidate_url for candidate_url in candidate_urls if candidate_url not in browser_candidates]
        direct_out = download_from_candidate_urls(
            direct_candidates,
            download_dir=download_dir,
            title=item_title,
            referer=work_page.url,
            timeout_ms=operation_timeout_ms,
        )
        if direct_out:
            return direct_out, "ok"
        return None, "no_download"
    except Exception as e:
        if is_target_closed_error(e):
            return None, "target_closed"
        return None, f"exception: {e}"
    finally:
        if created_page:
            try:
                work_page.close()
            except Exception:
                pass


def download_one_lanzou(
    page,
    url: str,
    pwd: str,
    download_dir: Path,
    title: str,
    timeout_ms: int,
    label: str = "",
):
    # Every nested network/browser fallback shares this absolute entry deadline.
    entry_cap_ms = max(60, int(os.getenv("LANZOU_ENTRY_TIMEOUT_SECONDS", "360"))) * 1000
    base_cap_ms = min(max(timeout_ms, 10000), 240000, entry_cap_ms)
    # Will re-scale after we know bundle count; provisional deadline first
    total_timeout_ms = base_cap_ms
    deadline_ts = time.monotonic() + (total_timeout_ms / 1000.0)
    page.set_default_timeout(min(total_timeout_ms, 12000))
    try:
        page.set_default_navigation_timeout(min(total_timeout_ms, navigation_timeout_cap_ms()))
    except Exception:
        pass
    raw_items: List[Dict[str, str]] = fetch_share_items_via_ajax(
        url, pwd=pwd, timeout_ms=min(timeout_left_ms(deadline_ts), total_timeout_ms)
    )
    items = pick_share_items(raw_items)
    if not items and timeout_left_ms(deadline_ts) > 0:
        safe_page_goto(page, url, deadline_ts=deadline_ts, label="share")
        fill_lanzou_password(page, pwd)
        wait_ms = min(1500, timeout_left_ms(deadline_ts))
        if wait_ms > 0:
            page.wait_for_timeout(wait_ms)
        raw_items = collect_share_items(page)
        items = pick_share_items(raw_items)
    if not items and pwd and timeout_left_ms(deadline_ts) > 0:
        fill_lanzou_password(page, pwd)
        wait_ms = min(1500, timeout_left_ms(deadline_ts))
        if wait_ms > 0:
            page.wait_for_timeout(wait_ms)
        raw_items = collect_share_items(page)
        items = pick_share_items(raw_items)
    if not items:
        return None, "no_target_item"

    has_bundle = any(i.get("kind") == "bundle" for i in items)
    if has_bundle:
        # ALL 合集 (合集1/2/3…); never download single-volume epubs when bundles exist
        target_items = [i for i in items if i.get("kind") == "bundle"]
        skipped_singles = sum(1 for i in (raw_items or []) if i.get("kind") == "epub")
        names = ", ".join((i.get("text") or "?") for i in target_items[:12])
        log(
            f"[INFO] 检测到合集 x{len(target_items)}，全部下载、跳过单本"
            + (f" x{skipped_singles}" if skipped_singles else "")
            + (f": {names}" if names else "")
        )
    else:
        # No 合集 on share page: fall back to individual epubs (still capped)
        target_items = [i for i in items if i.get("kind") == "epub"][:8] or items[:1]
        log(f"[INFO] 未找到合集，回退下载单本 x{len(target_items)}")

    # Extend multi-bundle entries without exceeding the configured entry deadline.
    if has_bundle and len(target_items) > 1:
        multi_cap_ms = min(entry_cap_ms, max(base_cap_ms, 180000 * len(target_items)))
        if multi_cap_ms > total_timeout_ms:
            extra = (multi_cap_ms - total_timeout_ms) / 1000.0
            deadline_ts += extra
            total_timeout_ms = multi_cap_ms
            log(f"[INFO] 多合集条目，放宽超时至 {total_timeout_ms // 1000}s")

    downloaded: List[Path] = []
    last_status = "no_download"
    item_retries = item_download_retries()
    for idx, item in enumerate(target_items):
        out_path = None
        item_status = "no_download"
        for attempt in range(1, item_retries + 1):
            left_ms = timeout_left_ms(deadline_ts)
            if left_ms <= 0:
                last_status = "timeout"
                log(f"[WARN] 蓝奏条目总超时，停止: {title} (已下 {len(downloaded)}/{len(target_items)})")
                break
            # Multi-合集 shares need more time per file (large zips)
            per_item_timeout_ms = min(180000 if item.get("kind") == "bundle" else 90000, left_ms)
            item_deadline_ts = time.monotonic() + (per_item_timeout_ms / 1000.0)
            try:
                out_path, item_status = download_share_item(
                    page=page,
                    item=item,
                    share_url=url,
                    download_dir=download_dir,
                    title=title,
                    deadline_ts=item_deadline_ts,
                    timeout_ms=per_item_timeout_ms,
                )
            except Exception as e:
                out_path, item_status = None, f"exception: {e}"
                log(f"[WARN] 蓝奏文件异常: {item.get('text', item)} err={e}")
            if out_path:
                break
            if (
                attempt < item_retries
                and is_retryable_item_status(item_status)
                and timeout_left_ms(deadline_ts) > 0
            ):
                log(
                    f"[WARN] 蓝奏文件将重试 ({attempt}/{item_retries}): "
                    f"{item.get('text', item)} status={item_status}"
                )
                continue
            break
        if out_path:
            out_path = finalize_download_path(
                Path(out_path),
                download_dir,
                label,
                original_name=item.get("text") or Path(out_path).name,
            )
            downloaded.append(out_path)
            log(
                f"[INFO] 已下载蓝奏文件 ({idx + 1}/{len(target_items)}): "
                f"{item['text']} -> {out_path}"
            )
            continue
        if last_status != "timeout":
            if item_status and item_status != "no_download":
                last_status = item_status
            log(f"[WARN] 蓝奏文件下载失败，继续下一项: {item.get('text', item)} status={item_status}")
        if last_status == "timeout":
            break

    if downloaded:
        return downloaded, "ok"
    return None, last_status


def find_extract_tool() -> Optional[List[str]]:
    for cmd in ("7z", "7zz"):
        path = shutil.which(cmd)
        if path:
            return [path]
    path = shutil.which("unar")
    if path:
        return [path]
    return None


def collect_epubs(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*.epub") if p.is_file() and not is_zht_name(p.name))


def extract_archive(archive_path: Path, extract_dir: Path) -> List[Path]:
    extract_dir.mkdir(parents=True, exist_ok=True)
    suffix = archive_path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(extract_dir)
        return collect_epubs(extract_dir)

    tool = find_extract_tool()
    if tool is None:
        raise RuntimeError("缺少 7z/7zz/unar，无法提取 .7z 或 .rar 压缩包")

    if tool[0].endswith("unar"):
        cmd = [tool[0], "-output-directory", str(extract_dir), str(archive_path)]
    else:
        cmd = [tool[0], "x", str(archive_path), f"-o{extract_dir}", "-y"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"解压失败: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
    return collect_epubs(extract_dir)


def copy_epubs_to_output(extracted_epubs: Iterable[Path], epub_output_dir: Path) -> List[Path]:
    epub_output_dir.mkdir(parents=True, exist_ok=True)
    saved: List[Path] = []
    for src in extracted_epubs:
        if is_zht_name(src.name):
            continue
        target = unique_path(epub_output_dir, safe_filename(src.name))
        shutil.copy2(src, target)
        saved.append(target)
    return saved


def build_entry_title(entry: Dict[str, str]) -> str:
    title = entry["title"]
    volume = entry["volume"]
    remark = entry["remark"]
    parts = [title]
    if volume:
        parts.append(f"({volume})")
    if remark:
        parts.append(f"[{remark}]")
    return " ".join(parts)


def entry_updated_after_baseline(entry: Dict[str, str], baseline_created_at: str) -> bool:
    update_value = (entry.get("update") or "").strip()
    if not update_value or not baseline_created_at:
        return False
    try:
        update_day = datetime.strptime(update_value, "%Y-%m-%d").date()
        baseline_day = datetime.strptime(baseline_created_at, "%Y-%m-%d %H:%M:%S").date()
    except ValueError:
        return False
    return update_day > baseline_day


def make_state_entry(
    entry: Dict[str, str],
    title: str,
    archive_path: Optional[Path],
    epubs: Iterable[Path],
    status: str,
    archive_paths: Optional[Iterable[Path]] = None,
) -> Dict[str, object]:
    archive_path_list = [str(p) for p in (archive_paths or [])]
    if archive_path and str(archive_path) not in archive_path_list:
        archive_path_list.insert(0, str(archive_path))
    return {
        "title": title,
        "archive_path": str(archive_path) if archive_path else "",
        "archive_paths": archive_path_list,
        "epubs": [str(p) for p in epubs],
        "status": status,
        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "entry_signature": build_entry_signature(entry),
        "entry_update": (entry.get("update") or "").strip(),
        "entry_volume": (entry.get("volume") or "").strip(),
        "entry_remark": (entry.get("remark") or "").strip(),
    }


def run(args) -> int:
    merged_csv_path = Path(args.merged_csv)
    dl_txt_path = Path(args.dl_txt)
    output_dir = Path(args.output_dir)
    archive_dir = output_dir / "archives"
    extract_root = output_dir / "_extract"
    epub_dir = output_dir / "epubs"
    state_path = output_dir / "state.json"

    prefix = parse_prefix(dl_txt_path)
    entries = load_entries(merged_csv_path, limit=args.limit, name_contains=args.name_contains)
    if not entries:
        log("[INFO] 没有可处理的蓝奏条目。")
        return 0

    state = load_state(state_path)
    labels_state = state.setdefault("labels", {})
    baseline_labels = set(state.setdefault("baseline_labels", []))
    baseline_entries = state.setdefault("baseline_entries", {})
    archive_dir.mkdir(parents=True, exist_ok=True)
    cleanup_orphan_archives(archive_dir, state)
    if not baseline_labels and not args.include_existing:
        baseline_labels = set(load_all_labels(merged_csv_path))
        state["baseline_labels"] = sorted(baseline_labels)
        state["baseline_entries"] = load_all_entry_signatures(merged_csv_path)
        state["baseline_created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(state_path, state)
        log(f"[INFO] 已建立初始基线，共 {len(baseline_labels)} 条。后续仅下载部署后新增的条目。")
        return 0

    from playwright.sync_api import sync_playwright

    ok_cnt = 0
    skip_cnt = 0
    fail_cnt = 0

    # Whole integrated downloader hard budget (env override allowed)
    run_budget_s = max(60, int(os.getenv("LANZOU_RUN_TIMEOUT_SECONDS", "600")))
    run_deadline_ts = time.monotonic() + run_budget_s
    log(f"[INFO] lanzou downloader budget={run_budget_s}s entries={len(entries)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.show_browser,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = browser.new_context(accept_downloads=True)
        try:
            context.set_default_timeout(12000)
            context.set_default_navigation_timeout(navigation_timeout_cap_ms())
        except Exception:
            pass
        for entry in entries:
            if timeout_left_ms(run_deadline_ts) <= 0:
                log(f"[WARN] 达到 lanzou 总超时 {run_budget_s}s，提前结束本轮下载")
                break
            label = entry["label"]
            title = build_entry_title(entry)
            current_signature = build_entry_signature(entry)
            existing = labels_state.get(label)
            if existing and not args.force:
                if existing.get("entry_signature") == current_signature:
                    log(f"[INFO] 跳过已处理条目: {title} ({label})")
                    skip_cnt += 1
                    continue
                log(f"[INFO] 检测到已处理条目元数据变化，重新下载: {title} ({label})")
            if (label in baseline_labels) and not args.include_existing and not args.force:
                baseline_signature = baseline_entries.get(label)
                if baseline_signature:
                    if baseline_signature == current_signature:
                        skip_cnt += 1
                        continue
                    log(f"[INFO] 检测到基线条目元数据变化，准备下载: {title} ({label})")
                elif entry_updated_after_baseline(entry, state.get("baseline_created_at", "")):
                    log(f"[INFO] 检测到基线条目在基线创建后更新，准备下载: {title} ({label})")
                else:
                    skip_cnt += 1
                    continue

            url = f"https://{prefix}/{label}"
            pwd = entry["pwd"]
            page = context.new_page()
            downloaded_paths: List[Path] = []
            status = "unknown"
            try:
                log(f"[INFO] 下载蓝奏条目: {title} ({url})")
                download_result, status = download_one_lanzou(
                    page=page,
                    url=url,
                    pwd=pwd,
                    download_dir=archive_dir,
                    title=title,
                    timeout_ms=min(args.timeout_ms, timeout_left_ms(run_deadline_ts)),
                    label=label,
                )
                if isinstance(download_result, list):
                    downloaded_paths = [Path(p) for p in download_result]
                elif download_result:
                    downloaded_paths = [Path(download_result)]
            except Exception as e:
                status = f"exception: {e}"
            finally:
                try:
                    page.close()
                except Exception:
                    pass

            if not downloaded_paths:
                log(f"[WARN] 下载失败: {title} status={status}")
                fail_cnt += 1
                continue

            direct_epubs = [p for p in downloaded_paths if p.suffix.lower() == ".epub"]
            archive_paths = [p for p in downloaded_paths if p.suffix.lower() != ".epub"]
            copied: List[Path] = []

            if direct_epubs:
                usable_epubs = [p for p in direct_epubs if not is_zht_name(p.name)]
                skipped_zht = len(direct_epubs) - len(usable_epubs)
                if skipped_zht:
                    log(f"[INFO] 跳过繁体 EPUB={skipped_zht}")
                copied.extend(copy_epubs_to_output(usable_epubs, epub_dir))
                if copied and not archive_paths:
                    labels_state[label] = make_state_entry(
                        entry=entry,
                        title=title,
                        archive_path=None,
                        archive_paths=[],
                        epubs=copied,
                        status="direct_epub",
                    )
                    save_state(state_path, state)
                    # Source files under archives/ are copies; epubs/ holds the keepers
                    cleanup_local_files(
                        [p for p in direct_epubs if p.parent.resolve() == archive_dir.resolve()],
                        reason=f"直接 EPUB 已复制 {label}",
                    )
                    log(f"[INFO] 完成: {title} 直接下载 EPUB={len(copied)}")
                    ok_cnt += 1
                    continue
                if not copied and not archive_paths:
                    labels_state[label] = make_state_entry(
                        entry=entry,
                        title=title,
                        archive_path=direct_epubs[0],
                        archive_paths=direct_epubs,
                        epubs=[],
                        status="skip_zht_epub",
                    )
                    save_state(state_path, state)
                    skip_cnt += 1
                    continue

            extract_dir = extract_root / safe_filename(label)
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)
            if archive_paths:
                try:
                    for archive_path in archive_paths:
                        item_extract_dir = extract_dir / safe_filename(archive_path.stem)
                        extracted = extract_archive(archive_path, item_extract_dir)
                        copied.extend(copy_epubs_to_output(extracted, epub_dir))
                except Exception as e:
                    log(f"[WARN] 压缩包已下载，但提取失败: {archive_paths} err={e}")
                    labels_state[label] = make_state_entry(
                        entry=entry,
                        title=title,
                        archive_path=archive_paths[0] if archive_paths else None,
                        archive_paths=archive_paths,
                        epubs=[],
                        status="archive_only",
                    )
                    save_state(state_path, state)
                    fail_cnt += 1
                    continue

            if not copied:
                log(f"[WARN] 下载文件里没有找到 EPUB: {downloaded_paths}")
                labels_state[label] = make_state_entry(
                    entry=entry,
                    title=title,
                    archive_path=downloaded_paths[0],
                    archive_paths=downloaded_paths,
                    epubs=[],
                    status="no_epub_found",
                )
                save_state(state_path, state)
                fail_cnt += 1
                continue

            # Keep only epub paths in state; archives are disposable after extract
            labels_state[label] = make_state_entry(
                entry=entry,
                title=title,
                archive_path=None,
                archive_paths=[],
                epubs=copied,
                status="ok",
            )
            save_state(state_path, state)
            shutil.rmtree(extract_dir, ignore_errors=True)
            # Auto-delete archives (and raw direct sources under archives/) after success
            to_delete = list(archive_paths) + [
                p for p in direct_epubs if p.parent.resolve() == archive_dir.resolve()
            ]
            cleanup_local_files(to_delete, reason=f"解压完成 {label}")
            log(f"[INFO] 完成: {title} EPUB={len(copied)}")
            ok_cnt += 1

        context.close()
        browser.close()

    log(f"[INFO] 任务结束 success={ok_cnt} skip={skip_cnt} fail={fail_cnt} output={output_dir}")
    return 0 if fail_cnt == 0 else 1


def parse_args():
    parser = argparse.ArgumentParser(description="Download Lanzou bundle archives and extract EPUB files")
    parser.add_argument("--merged-csv", required=True, help="Path to merged.csv from wenku8 out/")
    parser.add_argument("--dl-txt", required=True, help="Path to dl.txt from wenku8 out/")
    parser.add_argument("--output-dir", required=True, help="Directory for archives, extracted EPUBs and state")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N entries")
    parser.add_argument("--name-contains", default="", help="Only process entries whose title contains this text")
    parser.add_argument("--timeout-ms", type=int, default=90000, help="Per-entry timeout in milliseconds")
    parser.add_argument("--show-browser", action="store_true", help="Run Chromium in headed mode")
    parser.add_argument("--force", action="store_true", help="Re-download labels already recorded in state.json")
    parser.add_argument("--include-existing", action="store_true", help="Process existing labels instead of only entries after baseline")
    return parser.parse_args()


def main():
    args = parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()

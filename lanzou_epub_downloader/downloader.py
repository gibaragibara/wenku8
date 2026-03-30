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


def timeout_left_ms(deadline_ts: float, min_ms: int = 1) -> int:
    left = int((deadline_ts - time.monotonic()) * 1000)
    return left if left > min_ms else min_ms


def is_target_closed_error(err) -> bool:
    msg = str(err).lower()
    return (
        ("target page" in msg and "has been closed" in msg)
        or ("context or browser has been closed" in msg)
        or ("target closed" in msg)
    )


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
    timeout_s = max(10, int(timeout_ms / 1000))
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": referer or url,
    }
    try:
        resp = SESSION.get(url, headers=headers, timeout=timeout_s)
    except Exception:
        return None
    try:
        if resp.status_code >= 400:
            return None
        text = resp.text
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
        "Referer": resp.url if "resp" in locals() and getattr(resp, "url", None) else (referer or url),
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        ajax_resp = SESSION.post(ajax_url, headers=ajax_headers, data=payload, timeout=timeout_s)
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


def download_direct_file(url: str, download_dir: Path, title: str, referer: str, timeout_ms: int) -> Optional[Path]:
    timeout_s = max(10, int(timeout_ms / 1000))
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": random.choice(USER_AGENTS), "Referer": referer or url},
            timeout=timeout_s,
            allow_redirects=True,
            stream=True,
        )
    except Exception:
        return None
    try:
        if resp.status_code >= 400:
            return None
        filename = infer_filename_from_response(resp, fallback_title=title, fallback_ext=".zip")
        target = unique_path(download_dir, filename)
        size = 0
        with target.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                f.write(chunk)
                size += len(chunk)
        if size > 0:
            return target
        try:
            target.unlink()
        except Exception:
            pass
        return None
    finally:
        try:
            resp.close()
        except Exception:
            pass


def download_direct_file_via_api_request(api_request, url: str, download_dir: Path, title: str, referer: str, timeout_ms: int) -> Optional[Path]:
    try:
        resp = api_request.get(
            url,
            headers={
                "Referer": referer or url,
                "User-Agent": random.choice(USER_AGENTS),
            },
            timeout=timeout_ms,
        )
    except Exception:
        return None
    try:
        if not resp.ok:
            return None
        filename = infer_filename_from_headers(
            resp.headers,
            resp.url,
            fallback_title=title,
            fallback_ext=".zip",
        )
        target = unique_path(download_dir, filename)
        body = resp.body()
        if not body:
            return None
        target.write_bytes(body)
        return target
    finally:
        try:
            resp.dispose()
        except Exception:
            pass


def download_from_candidate_urls(candidates: Iterable[str], download_dir: Path, title: str, referer: str, timeout_ms: int) -> Optional[Path]:
    queue: List[Tuple[str, str]] = []
    for candidate in list(candidates)[:30]:
        queue.append((candidate, referer))
    seen = set()
    timeout_s = max(10, int(timeout_ms / 1000))
    while queue:
        url, current_referer = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if is_lanrar_file_page(url):
            resolved = resolve_lanrar_ajax_url(url, referer=current_referer or url, timeout_ms=timeout_ms)
            if resolved and resolved not in seen:
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
                    resolved = resolve_lanrar_ajax_url(resp.url, referer=current_referer or resp.url, timeout_ms=timeout_ms)
                    if resolved and resolved not in seen:
                        queue.insert(0, (resolved, resp.url))
                body = resp.text[:300000]
                nested = extract_candidate_urls_from_text(body, resp.url)
                for nested_url in nested:
                    if nested_url not in seen:
                        queue.append((nested_url, resp.url))
                continue

            filename = infer_filename_from_response(resp, fallback_title=title, fallback_ext=".zip")
            target = unique_path(download_dir, filename)
            size = 0
            with target.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    size += len(chunk)
            if size > 0:
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
    timeout_s = max(10, int(timeout_ms / 1000))
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
    bundle_items = [
        item
        for item in items
        if item["kind"] == "bundle" and not is_zht_item(item)
    ]
    plain_bundle_items = sorted(bundle_items, key=item_priority)
    epub_items = [
        item
        for item in items
        if item["kind"] == "epub" and not is_zht_item(item)
    ]
    return plain_bundle_items + sorted(epub_items, key=item_priority)


def open_share_item_page(context, item: Dict[str, str], timeout_ms: int):
    page = context.new_page()
    page.goto(item["href"], wait_until="domcontentloaded", timeout=min(timeout_ms, 45000))
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
            page.wait_for_timeout(2200)
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
    timeout_s = max(10, int(timeout_ms / 1000))
    item_headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": referer or item_url,
    }
    try:
        item_resp = SESSION.get(item_url, headers=item_headers, timeout=timeout_s)
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
    try:
        iframe_resp = SESSION.get(iframe_url, headers=iframe_headers, timeout=timeout_s)
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
    try:
        ajax_resp = SESSION.post(ajax_url, headers=ajax_headers, data=payload, timeout=timeout_s)
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
    candidate_url, candidate_referer, status = resolve_item_candidate_url(
        item["href"],
        referer=share_url,
        timeout_ms=timeout_ms,
    )
    if not candidate_url:
        return None, status

    lanrar_candidates = [candidate_url]
    if "&toolsdown" not in candidate_url.lower():
        lanrar_candidates.append(candidate_url + "&toolsdown")

    for lanrar_url in lanrar_candidates:
        final_url = resolve_lanrar_ajax_url(
            lanrar_url,
            referer=candidate_referer or share_url,
            timeout_ms=timeout_ms,
        )
        if not final_url:
            verify_page = None
            try:
                verify_page = context.new_page()
                verify_page.goto(
                    lanrar_url,
                    referer=candidate_referer or share_url,
                    wait_until="domcontentloaded",
                    timeout=min(timeout_ms, 45000),
                )
                verify_page.wait_for_timeout(1500)
                final_url = resolve_browser_download_link(
                    verify_page,
                    time.monotonic() + (max(min(timeout_ms, 45000), 5000) / 1000.0),
                )
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
        downloaded = download_direct_file_via_api_request(
            context.request,
            final_url,
            download_dir=download_dir,
            title=title,
            referer=lanrar_url,
            timeout_ms=timeout_ms,
        )
        if downloaded:
            return downloaded, "ok"
    return None, "no_download"


def download_one_lanzou(page, url: str, pwd: str, download_dir: Path, title: str, timeout_ms: int):
    deadline_ts = time.monotonic() + (max(timeout_ms, 10000) / 1000.0)
    page.set_default_timeout(min(timeout_ms, 15000))
    items = pick_share_items(fetch_share_items_via_ajax(url, pwd=pwd, timeout_ms=min(timeout_left_ms(deadline_ts), timeout_ms)))
    if not items:
        page.goto(url, wait_until="domcontentloaded", timeout=min(timeout_left_ms(deadline_ts), 45000))
        fill_lanzou_password(page, pwd)
        page.wait_for_timeout(1500)
        items = pick_share_items(collect_share_items(page))
    if not items and pwd:
        fill_lanzou_password(page, pwd)
        page.wait_for_timeout(1500)
        items = pick_share_items(collect_share_items(page))
    if not items:
        return None, "no_target_item"

    last_status = "no_download"
    for item in items:
        if timeout_left_ms(deadline_ts) <= 1:
            return None, "timeout"
        item_title = item["text"] if item["kind"] == "epub" else title
        direct_out, direct_status = download_item_via_http(
            page.context,
            item=item,
            share_url=url,
            download_dir=download_dir,
            title=item_title,
            timeout_ms=min(timeout_left_ms(deadline_ts), timeout_ms),
        )
        if direct_out:
            return direct_out, "ok"
        if direct_status and direct_status != "no_download":
            last_status = direct_status
        try:
            page.goto(
                item["href"],
                referer=url,
                wait_until="domcontentloaded",
                timeout=min(timeout_left_ms(deadline_ts), 45000),
            )
            page.wait_for_timeout(2000)
            candidate_urls = collect_page_download_candidates(page)
            # 把目标页 URL 也塞进候选队列，便于后续解析 toolsdown/ajax.php。
            try:
                candidate_urls.insert(0, page.url)
            except Exception:
                pass
            browser_candidates = []
            for browser_candidate in [page.url] + candidate_urls:
                if not is_lanrar_file_page(browser_candidate):
                    continue
                if "toolsdown" not in browser_candidate.lower():
                    continue
                if browser_candidate in browser_candidates:
                    continue
                browser_candidates.append(browser_candidate)
            for browser_candidate in browser_candidates:
                verify_page = None
                try:
                    verify_page = page.context.new_page()
                    verify_page.goto(
                        browser_candidate,
                        referer=page.url,
                        wait_until="domcontentloaded",
                        timeout=min(timeout_left_ms(deadline_ts), 45000),
                    )
                    verify_page.wait_for_timeout(1500)
                    browser_final_url = resolve_browser_download_link(verify_page, deadline_ts)
                    if not browser_final_url:
                        continue
                    direct_out = download_direct_file_via_api_request(
                        page.context.request,
                        browser_final_url,
                        download_dir=download_dir,
                        title=item_title,
                        referer=verify_page.url,
                        timeout_ms=min(timeout_left_ms(deadline_ts), timeout_ms),
                    )
                    if direct_out:
                        return direct_out, "ok"
                finally:
                    if verify_page is not None:
                        try:
                            verify_page.close()
                        except Exception:
                            pass
            direct_candidates = [url for url in candidate_urls if url not in browser_candidates]
            direct_out = download_from_candidate_urls(
                direct_candidates,
                download_dir=download_dir,
                title=item_title,
                referer=page.url,
                timeout_ms=min(timeout_left_ms(deadline_ts), timeout_ms),
            )
            if direct_out:
                return direct_out, "ok"
            last_status = "no_download"
        except Exception as e:
            if is_target_closed_error(e):
                last_status = "target_closed"
            else:
                last_status = f"exception: {e}"
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
) -> Dict[str, object]:
    return {
        "title": title,
        "archive_path": str(archive_path) if archive_path else "",
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

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.show_browser,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = browser.new_context(accept_downloads=True)
        for entry in entries:
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
            archive_path: Optional[Path] = None
            status = "unknown"
            try:
                log(f"[INFO] 下载蓝奏条目: {title} ({url})")
                archive_path, status = download_one_lanzou(
                    page=page,
                    url=url,
                    pwd=pwd,
                    download_dir=archive_dir,
                    title=title,
                    timeout_ms=args.timeout_ms,
                )
            except Exception as e:
                status = f"exception: {e}"
            finally:
                try:
                    page.close()
                except Exception:
                    pass

            if not archive_path:
                log(f"[WARN] 下载失败: {title} status={status}")
                fail_cnt += 1
                continue

            if archive_path.suffix.lower() == ".epub":
                if is_zht_name(archive_path.name):
                    log(f"[INFO] 跳过繁体 EPUB: {archive_path.name}")
                    labels_state[label] = make_state_entry(
                        entry=entry,
                        title=title,
                        archive_path=archive_path,
                        epubs=[],
                        status="skip_zht_epub",
                    )
                    save_state(state_path, state)
                    skip_cnt += 1
                    continue
                copied = copy_epubs_to_output([archive_path], epub_dir)
                labels_state[label] = make_state_entry(
                    entry=entry,
                    title=title,
                    archive_path=archive_path,
                    epubs=copied,
                    status="direct_epub",
                )
                save_state(state_path, state)
                log(f"[INFO] 完成: {title} 直接下载 EPUB={len(copied)}")
                ok_cnt += 1
                continue

            extract_dir = extract_root / safe_filename(label)
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)
            try:
                extracted = extract_archive(archive_path, extract_dir)
                copied = copy_epubs_to_output(extracted, epub_dir)
            except Exception as e:
                log(f"[WARN] 压缩包已下载，但提取失败: {archive_path} err={e}")
                labels_state[label] = make_state_entry(
                    entry=entry,
                    title=title,
                    archive_path=archive_path,
                    epubs=[],
                    status="archive_only",
                )
                save_state(state_path, state)
                fail_cnt += 1
                continue

            if not copied:
                log(f"[WARN] 压缩包里没有找到 EPUB: {archive_path}")
                labels_state[label] = make_state_entry(
                    entry=entry,
                    title=title,
                    archive_path=archive_path,
                    epubs=[],
                    status="no_epub_found",
                )
                save_state(state_path, state)
                fail_cnt += 1
                continue

            labels_state[label] = make_state_entry(
                entry=entry,
                title=title,
                archive_path=archive_path,
                epubs=copied,
                status="ok",
            )
            save_state(state_path, state)
            shutil.rmtree(extract_dir, ignore_errors=True)
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

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote

import requests

from i18n import t

# region Constants

GRAPHQL_URL = "https://assetstore.unity.com/api/graphql/batch"
DOWNLOAD_URL = "https://assetstore.unity.com/api/downloads"

COMMON_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7,zh-CN;q=0.6,ja;q=0.5",
    "origin": "https://assetstore.unity.com",
    "referer": "https://assetstore.unity.com/",
    "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "dnt": "1",
    "x-requested-with": "XMLHttpRequest",
    "x-source": "storefront",
}

SEARCH_QUERY = """query SearchMyAssets($page: Int, $pageSize: Int, $q: [String], $tagging: [String!], $assignFrom: [String!], $ids: [String!], $sortBy: Int, $reverse: Boolean, $other: String) {
  searchMyAssets(page: $page, pageSize: $pageSize, q: $q, tagging: $tagging, assignFrom: $assignFrom, ids: $ids, sortBy: $sortBy, reverse: $reverse, other: $other) {
    results {
      id
      orderId
      grantTime
      tagging
      assignFrom
      product {
        id
        productId
        itemId
        name
        mainImage {
          icon75
          icon
          __typename
        }
        publisher {
          id
          name
          __typename
        }
        publishNotes
        state
        currentVersion {
          name
          publishedDate
          __typename
        }
        downloadSize
        __typename
      }
      __typename
    }
    organizations
    total
    category {
      name
      count
      __typename
    }
    publisherSuggest {
      name
      count
      __typename
    }
    __typename
  }
}
"""

PRODUCT_QUERY = """query Product($id: ID!) {
  product(id: $id) {
    ...product
    packageInListHotness
    reviews(rows: 2, sortBy: "rating") {
      ...reviews
      __typename
    }
    __typename
  }
}

fragment product on Product {
  id
  productId
  itemId
  slug
  name
  description
  aiDescription
  elevatorPitch
  keyFeatures
  compatibilityInfo
  customLicense
  rating {
    average
    count
    __typename
  }
  currentVersion {
    id
    name
    publishedDate
    __typename
  }
  reviewCount
  downloadSize
  assetCount
  publisher {
    id
    name
    url
    supportUrl
    supportEmail
    gaAccount
    gaPrefix
    __typename
  }
  userOverview {
    lastDownloadAt: last_downloaded_at
    __typename
  }
  mainImage {
    big
    facebook
    small
    icon
    icon75
    __typename
  }
  originalPrice {
    itemId
    originalPrice
    finalPrice
    isFree
    discount {
      save
      percentage
      type
      saleType
      __typename
    }
    currency
    entitlementType
    __typename
  }
  images {
    type
    imageUrl
    thumbnailUrl
    __typename
  }
  category {
    id
    name
    slug
    longName
    __typename
  }
  firstPublishedDate
  publishNotes
  supportedUnityVersions
  state
  overlay
  overlayText
  plusProSale
  licenseText
  vspProperties {
    ... on ExternalVSPProduct {
      externalLink
      __typename
    }
    __typename
  }
  __typename
}

fragment reviews on Reviews {
  count
  canRate: can_rate
  canReply: can_reply
  canComment: can_comment
  hasCommented: has_commented
  totalEntries: total_entries
  lastPage: last_page
  comments {
    id
    date
    editable
    rating
    user {
      id
      name
      profileUrl
      avatar
      __typename
    }
    isHelpful: is_helpful {
      count
      score
      __typename
    }
    subject
    version
    full
    is_complimentary
    vote
    replies {
      id
      editable
      date
      version
      full
      user {
        id
        name
        profileUrl
        avatar
        __typename
      }
      isHelpful: is_helpful {
        count
        score
        __typename
      }
      __typename
    }
    __typename
  }
  __typename
}
"""

# endregion

# region UI


def clear_view():
    # Always try to clear. Some Windows terminals report stdout as non-TTY,
    # which made menus append like a feed instead of replacing the view.
    if os.name == "nt":
        os.system("cls")
    else:
        os.system("clear")
    # ANSI fallback for modern terminals and Windows Terminal.
    print("\033[2J\033[H", end="", flush=True)


def _char_display_width(ch):
    if unicodedata.combining(ch):
        return 0
    if unicodedata.category(ch)[0] == "C":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1


def _display_width(text):
    return sum(_char_display_width(ch) for ch in str(text))


def _fit_dialog_text(text, width):
    text = str(text)
    if width <= 0:
        return ""
    current_width = _display_width(text)
    if current_width <= width:
        return text + (" " * (width - current_width))
    if width <= 1:
        return "…"[:width]

    result = []
    used = 0
    target = width - 1
    for ch in text:
        ch_width = _char_display_width(ch)
        if used + ch_width > target:
            break
        result.append(ch)
        used += ch_width
    return "".join(result).rstrip() + "…" + (" " * max(0, target - used))


def render_dialog(title, content_lines=None, help_text="", width=72, truncate_content=True):
    content_lines = [str(line) for line in (content_lines or [])]
    help_text = str(help_text or "")
    terminal = shutil.get_terminal_size((100, 30))
    # Keep one column clear so terminals do not auto-wrap the right border into
    # a stray vertical line at the edge of the screen.
    max_width = max(30, terminal.columns - 1)
    max_content_lines = max(1, terminal.lines - 6)

    if truncate_content and len(content_lines) > max_content_lines:
        hidden = len(content_lines) - max_content_lines + 1
        content_lines = content_lines[: max_content_lines - 1] + [f"… {hidden} more item(s); search to narrow results"]

    width = max(width, _display_width(title) + 6, _display_width(help_text) + 6)
    for line in content_lines:
        width = max(width, _display_width(line) + 6)
    width = min(width, max_width)

    inner = width - 2
    text_width = inner - 1
    print("╭" + "─" * inner + "╮")
    print("│ " + _fit_dialog_text(title, text_width) + "│")
    print("├" + "─" * inner + "┤")
    if content_lines:
        for line in content_lines:
            print("│ " + _fit_dialog_text(line, text_width) + "│")
    else:
        print("│" + " " * inner + "│")
    print("├" + "─" * inner + "┤")
    print("│ " + _fit_dialog_text(help_text, text_width) + "│")
    print("╰" + "─" * inner + "╯")


# endregion

# region Library Fetch

class CookieInvalidError(Exception):
    pass


def load_config(path="config.json"):
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)
    return normalize_config(config)


def save_config(config, path="config.json"):
    config = normalize_config(dict(config))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")


def save_active_account(active_account, path="config.json"):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raw = {}
    raw["active_account"] = str(active_account or "").strip()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
        f.write("\n")


def normalize_cookie_input(cookie):
    cookie = str(cookie or "").strip()
    # Allow pasting either the raw header value or the full browser header line.
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    return " ".join(cookie.split())


def save_active_account_cookie(config, cookie, path="config.json"):
    config = normalize_config(config)
    active = config.get("active_account")
    for acc in config.get("accounts", []):
        if acc.get("name") == active:
            acc["cookie"] = normalize_cookie_input(cookie)
            break
    save_config(config, path=path)


def normalize_config(config):
    config = dict(config or {})
    accounts = config.get("accounts")
    if not isinstance(accounts, list) or len(accounts) == 0:
        legacy_cookie = config.get("cookie", "")
        accounts = [{"name": "Account 1", "cookie": legacy_cookie}]
        config["accounts"] = accounts

    cleaned = []
    for i, acc in enumerate(accounts, start=1):
        if not isinstance(acc, dict):
            continue
        name = str(acc.get("name") or "").strip() or f"Account {i}"
        cookie = normalize_cookie_input(acc.get("cookie") or "")
        download_dir = acc.get("download_dir")
        cleaned_acc = {"name": name, "cookie": cookie}
        if isinstance(download_dir, str) and download_dir.strip():
            cleaned_acc["download_dir"] = download_dir.strip()
        cleaned.append(cleaned_acc)
    if not cleaned:
        cleaned = [{"name": "Account 1", "cookie": str(config.get("cookie") or "")}]
    config["accounts"] = cleaned

    active = str(config.get("active_account") or "").strip()
    names = [a["name"] for a in cleaned]
    if not active or active not in names:
        config["active_account"] = names[0]

    return config


def get_active_cookie(config):
    config = normalize_config(config)
    active = config.get("active_account")
    for acc in config.get("accounts", []):
        if acc.get("name") == active:
            return acc.get("cookie") or ""
    return ""


def get_active_account(config):
    config = normalize_config(config)
    active = config.get("active_account")
    for acc in config.get("accounts", []):
        if acc.get("name") == active:
            return acc
    return config.get("accounts", [{}])[0] if config.get("accounts") else {}


def extract_csrf(cookie_str):
    cookie_str = str(cookie_str or "")
    for name in (
        "_csrf",
        "__Host-next-auth.csrf-token",
        "__Secure-next-auth.csrf-token",
        "next-auth.csrf-token",
    ):
        match = re.search(rf"(?:^|;\s*){re.escape(name)}=([^;]+)", cookie_str)
        if match:
            value = unquote(match.group(1))
            return value.split("|", 1)[0]
    return ""


def make_graphql_headers(config, operations):
    cookie = get_active_cookie(config)
    csrf = extract_csrf(cookie)
    return {
        **COMMON_HEADERS,
        "content-type": "application/json;charset=UTF-8",
        "cookie": cookie,
        "x-csrf-token": csrf,
        "operations": operations,
    }


def request_with_retry(method, url, retry, **kwargs):
    for attempt in range(1, retry + 1):
        try:
            resp = method(url, **kwargs)
            if resp.status_code in (400, 401, 403) or (url == GRAPHQL_URL and resp.status_code >= 500):
                raise CookieInvalidError(t("cookie_expired"))
            if resp.status_code >= 500 and attempt < retry:
                wait = 2**attempt
                print(t("server_error").format(resp.status_code, wait, attempt))
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < retry:
                wait = 2**attempt
                print(t("network_error").format(wait, attempt, e))
                time.sleep(wait)
            else:
                raise
    return resp


def fetch_asset_list_page(config, page, page_size=100):
    headers = make_graphql_headers(config, "SearchMyAssets")
    payload = [
        {
            "query": SEARCH_QUERY,
            "variables": {
                "page": page,
                "pageSize": page_size,
                "q": [],
                "tagging": [],
                "ids": [],
                "assignFrom": [],
                "sortBy": 7,
            },
            "operationName": "SearchMyAssets",
        }
    ]
    retry = config.get("retry", 3)
    resp = request_with_retry(
        requests.post,
        GRAPHQL_URL,
        retry,
        headers=headers,
        json=payload,
        timeout=config.get("timeout", 60),
    )
    return resp.json()


def fetch_product_details(config, product_ids):
    if not product_ids:
        return []
    operations = ",".join(["Product"] * len(product_ids))
    headers = make_graphql_headers(config, operations)
    payload = [
        {
            "query": PRODUCT_QUERY,
            "variables": {"id": pid},
            "operationName": "Product",
        }
        for pid in product_ids
    ]
    retry = config.get("retry", 3)
    resp = request_with_retry(
        requests.post,
        GRAPHQL_URL,
        retry,
        headers=headers,
        json=payload,
        timeout=config.get("timeout", 120),
    )
    return resp.json()


def load_existing_list(list_path="asset_list.jsonl"):
    pages = {}
    try:
        with open(list_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                pages[obj["page"]] = obj
    except FileNotFoundError:
        pass
    return pages


def load_existing_detail_ids(info_path="asset_info.jsonl"):
    ids = set()
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                pid = str(obj.get("id", ""))
                if pid:
                    ids.add(pid)
    except FileNotFoundError:
        pass
    return ids


def append_list_page(page_num, page_data, f_list):
    search_data = page_data[0]["data"]["searchMyAssets"]
    record = {**search_data, "page": page_num}
    f_list.write(json.dumps(record, ensure_ascii=False) + "\n")
    f_list.flush()


def append_detail_batch(details, f_info, f_ids, existing_ids):
    count = 0
    for item in details:
        product = item.get("data", {}).get("product")
        if not product:
            continue
        f_info.write(json.dumps(product, ensure_ascii=False) + "\n")
        pid = str(product["id"])
        if pid not in existing_ids:
            f_ids.write(pid + "\n")
            existing_ids.add(pid)
        count += 1
    f_info.flush()
    f_ids.flush()
    return count


def extract_product_ids_from_list(existing_pages):
    seen = set()
    result = []
    for page_num in sorted(existing_pages.keys()):
        for item in existing_pages[page_num].get("results", []):
            pid = str(item["product"]["id"])
            if pid not in seen:
                seen.add(pid)
                result.append(pid)
    return result

# endregion

# region Download

def load_info_map(info_path="asset_info.jsonl"):
    info_map = {}
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                product = json.loads(line)
                pid = str(product.get("id", ""))
                if pid:
                    info_map[pid] = {
                        "name": product.get("name", ""),
                        "size": int(product.get("downloadSize") or 0),
                    }
    except FileNotFoundError:
        pass
    return info_map


def _safe_account_slug(name: str) -> str:
    s = (name or "").strip().lower()
    if not s:
        return "account"
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "account"


def account_data_paths(config):
    config = normalize_config(config)
    slug = _safe_account_slug(str(config.get("active_account") or ""))
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    return (
        str(data_dir / f"asset_list.{slug}.jsonl"),
        str(data_dir / f"asset_info.{slug}.jsonl"),
        str(data_dir / f"asset_ids.{slug}.txt"),
    )


def format_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def format_eta(seconds):
    if seconds < 0 or seconds > 86400:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


_print_lock = threading.Lock()


def _download_progress_line(downloaded, total_size, speed, finished=False):
    if total_size and total_size > 0:
        if finished and downloaded < total_size:
            downloaded = total_size
        pct = min(int(downloaded * 100 / total_size) if total_size else 0, 100)
        bar_len = 25
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        eta = format_eta((total_size - downloaded) / speed) if speed > 0 else "--:--"
        return (
            f"{bar} {pct:3d}%  {format_size(downloaded)}/{format_size(total_size)}"
            f"  {format_size(speed)}/s  ETA {eta}"
        )
    return t("downloaded_no_total").format(format_size(downloaded), format_size(speed))


def print_progress(downloaded, total_size, speed, finished=False):
    status = _download_progress_line(downloaded, total_size, speed, finished=finished)
    with _print_lock:
        if finished:
            print(status)
        else:
            print(status, end="\r", flush=True)


def _downloaded_size_from_message(message):
    match = re.search(r"\(([^()]+)\)\s*$", str(message or ""))
    return match.group(1) if match else "0 B"


def render_download_progress(download_dir, asset_name, downloaded=0, total_size=0, speed=0, finished=False, message=""):
    lines = [
        t("download_dir").format(_display_download_dir(Path(download_dir))),
        t("pending_download").format(asset_name),
    ]
    if message:
        lines.append(message)
    else:
        lines.append(_download_progress_line(downloaded, total_size, speed, finished=finished))
    clear_view()
    render_dialog(t("download_title"), lines, t("press_enter_continue") if finished or message else "")


def _bar_progress_line(done, total, unit=""):
    total = int(total or 0)
    done = int(done or 0)
    if total <= 0:
        pct = 0
    else:
        pct = min(int(done * 100 / total), 100)
    bar_len = 25
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    suffix = f" {unit}" if unit else ""
    return f"{bar} {pct:3d}%  {done}/{total}{suffix}"


def _print_bar_progress(done, total, label, finished=False):
    with _print_lock:
        status = _bar_progress_line(done, total)
        if finished:
            print(status)
        else:
            print(status, end="\r", flush=True)


def _render_fetch_progress(title, done, total, footer="", complete_text=""):
    lines = [t("search_query_help"), _bar_progress_line(done, total)]
    if complete_text:
        lines.append(complete_text)
    clear_view()
    render_dialog(title, lines, footer)


def render_extract_progress(extract_root, package_name, done=0, total=0, complete_text="", message=""):
    lines = [
        t("extraction_dir").format(_display_download_dir(Path(extract_root))),
        t("pending_download").format(package_name),
    ]
    if message:
        lines.append(message)
    elif complete_text:
        lines.append(complete_text)
    else:
        lines.append(_bar_progress_line(done, total, "Files"))
    clear_view()
    render_dialog(t("extract_title"), lines, t("press_enter_continue") if complete_text or message else "")


def parse_filename(response, asset_id):
    cd = response.headers.get("content-disposition", "")
    match = re.search(r'filename="(.+?)"', cd)
    if match:
        return unquote(match.group(1))
    match = re.search(r"filename\*=UTF-8''(.+)", cd)
    if match:
        return unquote(match.group(1))
    return f"{asset_id}.unitypackage"


def safe_package_filename(asset_name, asset_id):
    name = str(asset_name or "").strip()
    if name.lower().endswith(".unitypackage"):
        name = name[: -len(".unitypackage")]
    if not name:
        name = str(asset_id)

    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = str(asset_id)

    # Avoid Windows reserved device names even when running on other platforms.
    if name.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }:
        name = f"{name}_{asset_id}"

    return f"{name[:220]}.unitypackage"


def _desired_filename_for_asset(asset_id, info_map):
    return safe_package_filename(info_map.get(asset_id, {}).get("name", ""), asset_id)


def download_asset(asset_id, config, download_dir, total_size=0, desired_filename=None, progress_context=None):
    url = f"{DOWNLOAD_URL}/{asset_id}"
    headers = {
        **COMMON_HEADERS,
        "accept": "*/*",
        "cookie": get_active_cookie(config),
        "accept-encoding": "gzip, deflate, br, zstd",
    }
    for key in ["content-type", "origin", "x-requested-with", "x-source", "dnt"]:
        headers.pop(key, None)

    timeout = config.get("timeout", 300)
    retry = config.get("retry", 3)
    cache_dir = download_dir / ".cache"
    cache_dir.mkdir(exist_ok=True)
    meta_path = cache_dir / f"{asset_id}.meta"

    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        cached_filename = meta.get("filename", "")
        if cached_filename:
            filepath = download_dir / cached_filename
            if filepath.exists():
                if desired_filename and cached_filename != desired_filename:
                    desired_path = download_dir / desired_filename
                    if not desired_path.exists():
                        filepath.rename(desired_path)
                    meta_path.write_text(
                        json.dumps({"filename": desired_filename}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    return asset_id, True, t("exists_skip").format(desired_filename)
                return asset_id, True, t("exists_skip").format(cached_filename)

    for attempt in range(1, retry + 1):
        try:
            tmp_path = None
            resumed_bytes = 0
            req_headers = dict(headers)

            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                cached_filename = meta.get("filename", "")
                if cached_filename:
                    tmp_path = (download_dir / cached_filename).with_suffix(
                        (download_dir / cached_filename).suffix + ".tmp"
                    )
                    if tmp_path.exists():
                        resumed_bytes = tmp_path.stat().st_size

            if resumed_bytes > 0:
                req_headers["Range"] = f"bytes={resumed_bytes}-"

            resp = requests.get(url, headers=req_headers, stream=True, timeout=timeout)

            if resp.status_code == 401:
                return asset_id, False, t("cookie_expired")
            if resp.status_code == 403:
                return asset_id, False, t("no_permission")
            if resp.status_code == 404:
                return asset_id, False, t("not_found")

            if resp.status_code == 416:
                filename = desired_filename or parse_filename(resp, asset_id)
                filepath = download_dir / filename
                if tmp_path and tmp_path.exists():
                    tmp_path.rename(filepath)
                    return asset_id, True, t("resume_full").format(filename)

            resp.raise_for_status()

            filename = desired_filename or parse_filename(resp, asset_id)
            filepath = download_dir / filename

            meta_path.write_text(
                json.dumps({"filename": filename}, ensure_ascii=False),
                encoding="utf-8",
            )

            if filepath.exists():
                return asset_id, True, t("exists_skip").format(filename)

            tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")

            is_resumed = resp.status_code == 206
            if is_resumed:
                mode = "ab"
            else:
                resumed_bytes = 0
                mode = "wb"

            effective_total = total_size
            if not effective_total:
                content_range = resp.headers.get("Content-Range", "")
                if content_range:
                    m = re.search(r"/(\d+)", content_range)
                    if m:
                        effective_total = int(m.group(1))
                if not effective_total:
                    cl = resp.headers.get("Content-Length")
                    if cl:
                        effective_total = int(cl) + resumed_bytes

            downloaded = resumed_bytes
            start_time = time.time()

            with open(tmp_path, mode) as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = (downloaded - resumed_bytes) / elapsed if elapsed > 0 else 0
                    if progress_context:
                        now = time.time()
                        if now - progress_context.get("last_render", 0) >= 0.15:
                            progress_context["last_render"] = now
                            render_download_progress(
                                progress_context["download_dir"],
                                progress_context["asset_name"],
                                downloaded,
                                effective_total,
                                speed,
                            )
                    else:
                        print_progress(downloaded, effective_total, speed)

            elapsed = time.time() - start_time
            speed = (downloaded - resumed_bytes) / elapsed if elapsed > 0 else 0
            if progress_context:
                render_download_progress(
                    progress_context["download_dir"],
                    progress_context["asset_name"],
                    downloaded,
                    effective_total,
                    speed,
                    finished=True,
                )
            else:
                print_progress(downloaded, effective_total, speed, finished=True)

            tmp_path.rename(filepath)

            resumed_tag = t("resumed") if is_resumed else ""
            return (
                asset_id,
                True,
                t("done").format(resumed_tag, filename, format_size(downloaded)),
            )

        except requests.RequestException as e:
            if attempt < retry:
                wait = 2**attempt
                with _print_lock:
                    print(t("attempt_fail").format(asset_id, attempt, wait, e))
                time.sleep(wait)
            else:
                return asset_id, False, t("fail_retry").format(retry, e)

    return asset_id, False, t("unknown_error")


def _build_local_file_index(download_dir):
    index = {}
    for f in download_dir.glob("*.unitypackage"):
        index[f.name.lower()] = f
    return index


def _pre_check_downloads(asset_ids, download_dir, cache_dir, info_map):
    local_files = _build_local_file_index(download_dir)
    skipped = []
    pending = []

    for aid in asset_ids:
        meta_path = cache_dir / f"{aid}.meta"
        info = info_map.get(aid, {})
        desired_filename = _desired_filename_for_asset(aid, info_map)

        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            cached_filename = meta.get("filename", "")
            if cached_filename:
                filepath = download_dir / cached_filename
                if filepath.exists():
                    if cached_filename != desired_filename:
                        desired_path = download_dir / desired_filename
                        if not desired_path.exists():
                            filepath.rename(desired_path)
                            local_files.pop(cached_filename.lower(), None)
                            local_files[desired_filename.lower()] = desired_path
                        meta_path.write_text(
                            json.dumps({"filename": desired_filename}, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        skipped.append((aid, desired_filename))
                    else:
                        skipped.append((aid, cached_filename))
                    continue

        product_name = info.get("name", "")
        if product_name:
            desired_lower = desired_filename.lower()
            if desired_lower in local_files:
                fpath = local_files[desired_lower]
                meta_path.write_text(
                    json.dumps({"filename": fpath.name}, ensure_ascii=False),
                    encoding="utf-8",
                )
                skipped.append((aid, fpath.name))
                continue

            name_lower = product_name.lower()
            for fname_lower, fpath in local_files.items():
                if name_lower in fname_lower:
                    meta_path.write_text(
                        json.dumps({"filename": fpath.name}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    skipped.append((aid, fpath.name))
                    break
            else:
                pending.append(aid)
        else:
            pending.append(aid)

    return skipped, pending


def _pending_display_filename(asset_id, info_map):
    return _desired_filename_for_asset(asset_id, info_map)


def _display_download_dir(path: Path) -> str:
    path = path.resolve()
    cwd = Path.cwd().resolve()
    try:
        rel = path.relative_to(cwd)
        s = rel.as_posix()
        if s == ".":
            return path.as_posix()
        return "/" + s
    except ValueError:
        return path.as_posix()


def _is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        with open("/proc/version", "r", encoding="utf-8") as f:
            v = f.read().lower()
        return "microsoft" in v or "wsl" in v
    except OSError:
        return False


def _open_folder(path: Path) -> None:
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    path_str = os.fspath(path)
    if sys.platform == "win32":
        os.startfile(path_str)
        return
    if sys.platform == "darwin":
        subprocess.run(["open", path_str], check=False)
        return

    for opener in ("xdg-open", "wslview"):
        try:
            subprocess.run([opener, path_str], check=False)
            return
        except FileNotFoundError:
            continue

    if _is_wsl():
        explorer = Path("/mnt/c/Windows/explorer.exe")
        if explorer.is_file():
            try:
                win = subprocess.run(
                    ["wslpath", "-w", path_str],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                subprocess.run([str(explorer), win], check=False)
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass


def _prepare_download_environment(config):
    acc = get_active_account(config)
    download_dir = Path(
        acc.get("download_dir") or config.get("download_dir", "./downloads")
    )
    download_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = download_dir / ".cache"
    cache_dir.mkdir(exist_ok=True)
    for old_meta in download_dir.glob(".*.meta"):
        new_name = old_meta.name[1:]
        new_path = cache_dir / new_name
        if not new_path.exists():
            old_meta.rename(new_path)
        else:
            old_meta.unlink()
    return download_dir, cache_dir


def _clean_display_name(name):
    return re.sub(r"\s+", " ", str(name or "")).strip()


def _truncate_text(text, max_width):
    text = str(text)
    if _display_width(text) <= max_width:
        return text
    if max_width <= 1:
        return "…"[:max_width]

    result = []
    used = 0
    target = max_width - 1
    for ch in text:
        ch_width = _char_display_width(ch)
        if used + ch_width > target:
            break
        result.append(ch)
        used += ch_width
    return "".join(result).rstrip() + "…"


def _asset_id_name_lines(info_map, asset_ids=None):
    def sort_key(pid):
        try:
            return (0, int(pid))
        except ValueError:
            return (1, pid)

    ids_sorted = sorted(asset_ids if asset_ids is not None else info_map.keys(), key=sort_key)
    if not ids_sorted:
        return []
    id_width = max(len(str(pid)) for pid in ids_sorted)
    # Keep asset names inside the dialog so long names/extra whitespace do not push the right border.
    max_line_width = max(30, shutil.get_terminal_size((100, 30)).columns - 9)
    name_width = max(10, max_line_width - id_width - 4)
    lines = []
    for pid in ids_sorted:
        name = _truncate_text(_clean_display_name(info_map[pid].get("name", "")), name_width)
        lines.append(f"  {pid:>{id_width}}  {name}")
    return lines


def _render_asset_results(title, asset_lines):
    # Long result lists scroll more cleanly without a boxed right border.
    print(title)
    print()
    for line in asset_lines:
        print(line)
    print()
    print(t("press_enter_continue"))


def list_assets_id_name(config=None):
    if config is None:
        config = load_config()
    _, info_path, _ = account_data_paths(config)
    info_map = load_info_map(info_path)
    if not info_map:
        print(t("no_asset_info"))
        return

    for line in _asset_id_name_lines(info_map):
        print(line)


def search_assets_by_query(config):
    clear_view()
    _, info_path, _ = account_data_paths(config)
    try:
        ok = run_fetch_list(config, footer=t("enter_search_query").strip())
    except CookieInvalidError:
        clear_view()
        render_dialog(
            t("search_title"),
            [t("cookie_expired"), t("cookie_invalid_help")],
            t("press_enter_continue"),
        )
        return
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        clear_view()
        render_dialog(
            t("search_title"),
            [f"{t('fetch_failed')}: HTTP {status}", t("fetch_failed_help")],
            t("press_enter_continue"),
        )
        return
    except requests.RequestException as e:
        clear_view()
        render_dialog(
            t("search_title"),
            [t("fetch_failed"), str(e)],
            t("press_enter_continue"),
        )
        return
    info_map = load_info_map(info_path)
    if not ok and not info_map:
        print(t("no_asset_info"))
        return
    if not info_map:
        print(t("no_asset_info"))
        return

    raw = input().strip()
    if not raw:
        clear_view()
        asset_lines = _asset_id_name_lines(info_map)
        _render_asset_results(f"Found {len(asset_lines)} assets", asset_lines)
        return

    needle = raw.lower()

    matched = []
    for pid, info in info_map.items():
        name = (info.get("name") or "").lower()
        pid_str = str(pid)
        if needle in name or needle in pid_str.lower():
            matched.append(pid)

    if not matched:
        clear_view()
        render_dialog("Found 0 assets", [t("search_no_results")], t("press_enter_continue"))
        return

    clear_view()
    asset_lines = _asset_id_name_lines(info_map, matched)
    _render_asset_results(f"Found {len(asset_lines)} assets", asset_lines)


def download_single_by_id(config, asset_id_str):
    asset_id_str = asset_id_str.strip()
    if not asset_id_str:
        return
    if not asset_id_str.isdigit():
        return

    download_dir, cache_dir = _prepare_download_environment(config)
    _, info_path, _ = account_data_paths(config)
    info_map = load_info_map(info_path)
    skipped, pending_ids = _pre_check_downloads(
        [asset_id_str], download_dir, cache_dir, info_map
    )
    if skipped:
        for aid, fname in skipped:
            render_download_progress(download_dir, fname, message=t("exists_skip").format(fname))
        return

    if not pending_ids:
        return

    aid = pending_ids[0]
    desired_filename = _pending_display_filename(aid, info_map)
    render_download_progress(download_dir, desired_filename, 0, info_map.get(aid, {}).get("size", 0), 0)
    asset_id, ok, msg = download_asset(
        aid,
        config,
        download_dir,
        info_map.get(aid, {}).get("size", 0),
        desired_filename=desired_filename,
        progress_context={
            "download_dir": download_dir,
            "asset_name": desired_filename,
            "last_render": 0,
        },
    )
    downloaded_size = _downloaded_size_from_message(msg) if ok else "0 B"
    render_download_progress(
        download_dir,
        desired_filename,
        message=t("download_done").format(downloaded_size, 1 if ok else 0, 0 if ok else 1),
    )


def _extract_unitypackage_with_progress(package_path, output_path, encoding="utf-8", progress_context=None):
    import os
    import shutil
    import tempfile

    import tarsafe

    output_path = str(Path(output_path).resolve())
    package_path = str(package_path)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with tarsafe.open(name=package_path, encoding=encoding) as upkg:
            upkg.extractall(tmp_dir)

        items = []
        for dir_entry in os.scandir(tmp_dir):
            asset_entry_dir = os.path.join(tmp_dir, dir_entry.name)
            pathname_file = os.path.join(asset_entry_dir, "pathname")
            asset_file = os.path.join(asset_entry_dir, "asset")
            if not os.path.exists(pathname_file) or not os.path.exists(asset_file):
                continue
            with open(pathname_file, encoding=encoding) as f:
                pathname = f.readline()
            pathname = pathname[:-1] if pathname and pathname[-1] == "\n" else pathname
            if os.name == "nt":
                pathname = re.sub(r'[>:"|?*]', "_", pathname)
            asset_out_path = os.path.join(output_path, pathname)
            out_resolved = Path(output_path).resolve()
            if out_resolved not in Path(asset_out_path).resolve().parents:
                print(
                    f"WARNING: Skipping '{dir_entry.name}' as '{asset_out_path}' is outside of '{output_path}'."
                )
                continue
            items.append((asset_entry_dir, asset_out_path))

        total = len(items)
        if total == 0:
            return 0

        if progress_context:
            render_extract_progress(
                progress_context["extract_root"],
                progress_context["package_name"],
                0,
                total,
            )

        for i, (asset_entry_dir, asset_out_path) in enumerate(items, start=1):
            dest_dir = os.path.dirname(asset_out_path)
            if dest_dir:
                os.makedirs(dest_dir, exist_ok=True)
            shutil.move(os.path.join(asset_entry_dir, "asset"), asset_out_path)
            if progress_context:
                now = time.time()
                if i == total or now - progress_context.get("last_render", 0) >= 0.15:
                    progress_context["last_render"] = now
                    render_extract_progress(
                        progress_context["extract_root"],
                        progress_context["package_name"],
                        i,
                        total,
                    )
            else:
                pct = 100 * i // total
                bar_len = 25
                filled = int(bar_len * pct / 100)
                bar = "█" * filled + "░" * (bar_len - filled)
                with _print_lock:
                    print(
                        f"\r{bar} {pct:3d}%  {i}/{total} Files",
                        end="",
                        flush=True,
                    )
        if not progress_context:
            with _print_lock:
                print()
        return total


def extract_assets_menu(config):
    clear_view()
    download_dir, _ = _prepare_download_environment(config)
    download_dir = download_dir.resolve()
    extract_root = download_dir.parent / "extracts"

    packages = sorted(
        download_dir.glob("*.unitypackage"),
        key=lambda p: p.name.lower(),
    )
    if not packages:
        content_lines = [t("no_unitypackages")]
    else:
        w = len(str(len(packages)))
        content_lines = [f"  {i:>{w}}. {p.name}" for i, p in enumerate(packages, start=1)]

    render_dialog(t("extract_title"), content_lines, t("enter_extract_index").strip())
    raw = input().strip()
    if raw == "":
        return False
    if raw == ".":
        _open_folder(extract_root)
        return False
    if not packages:
        print(t("invalid_extract_index"))
        return True
    if not raw.isdigit():
        print(t("invalid_extract_index"))
        return True
    n = int(raw)
    if n < 1 or n > len(packages):
        print(t("invalid_extract_index"))
        return True

    package_path = packages[n - 1]
    extract_root.mkdir(parents=True, exist_ok=True)
    out_dir = extract_root / package_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        render_extract_progress(extract_root, package_path.name, 0, 0)
        count = _extract_unitypackage_with_progress(
            str(package_path),
            str(out_dir),
            progress_context={
                "extract_root": extract_root,
                "package_name": package_path.name,
                "last_render": 0,
            },
        )
        render_extract_progress(
            extract_root,
            package_path.name,
            count,
            count,
            complete_text=t("extract_complete").format(count, 0),
        )
    except ImportError:
        render_extract_progress(
            extract_root,
            package_path.name,
            message=t("extractor_missing"),
            complete_text=t("extract_complete").format(0, 1),
        )
    except Exception as e:
        render_extract_progress(
            extract_root,
            package_path.name,
            message=t("extract_failed").format(e),
            complete_text=t("extract_complete").format(0, 1),
        )
    return True

# endregion

# region Main

def _fetch_list_page_task(config, page, page_size):
    page_data = fetch_asset_list_page(config, page, page_size)
    search_data = page_data[0]["data"]["searchMyAssets"]
    return page, {**search_data, "page": page}


def _fetch_detail_batch_task(config, batch, batch_num):
    details = fetch_product_details(config, batch)
    products = []
    for item in details:
        product = item.get("data", {}).get("product")
        if product:
            products.append(product)
    return batch_num, products


def run_fetch_list(config, detail_batch_size=100, footer=""):
    page_size = 100
    list_path, info_path, ids_path = account_data_paths(config)
    max_workers = config.get("max_workers", 3)
    _file_lock = threading.Lock()

    fetch_title = t("search_title")

    existing_pages = load_existing_list(list_path)

    if 0 in existing_pages:
        total = existing_pages[0]["total"]
    else:
        first_page = fetch_asset_list_page(config, 0, page_size)
        total = first_page[0]["data"]["searchMyAssets"]["total"]
        record = {**first_page[0]["data"]["searchMyAssets"], "page": 0}
        with open(list_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        existing_pages[0] = record

    total_pages = math.ceil(total / page_size)
    missing_pages = [p for p in range(total_pages) if p not in existing_pages]

    # Single unified progress bar across both phases (pages + detail batches).
    # We can estimate detail batches from the total item count (page 0).
    est_total_batches = math.ceil(total / detail_batch_size) if total else 0
    progress_total = max(total_pages + est_total_batches, 1)
    progress_done = len(existing_pages)
    _render_fetch_progress(fetch_title, progress_done, progress_total, footer)

    if missing_pages:
        with open(list_path, "a", encoding="utf-8") as f:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_fetch_list_page_task, config, page, page_size): page
                    for page in missing_pages
                }
                for future in as_completed(futures):
                    page = futures[future]
                    try:
                        page_num, record = future.result()
                        with _file_lock:
                            f.write(json.dumps(record, ensure_ascii=False) + "\n")
                            f.flush()
                            existing_pages[page_num] = record
                        progress_done = len(existing_pages)
                        _render_fetch_progress(fetch_title, progress_done, progress_total, footer)
                    except requests.RequestException as e:
                        _render_fetch_progress(fetch_title, progress_done, progress_total, footer)

        still_missing = [p for p in range(total_pages) if p not in existing_pages]
        if still_missing:
            _render_fetch_progress(
                fetch_title,
                len(existing_pages),
                progress_total,
                footer,
                t("fetch_complete").format(len(existing_pages), len(still_missing)),
            )
            print(t("still_missing").format(len(still_missing), still_missing))
            print(t("rerun"))
            return False

    all_product_ids = extract_product_ids_from_list(existing_pages)
    already_fetched = load_existing_detail_ids(info_path)
    pending_ids = [pid for pid in all_product_ids if pid not in already_fetched]

    if not pending_ids:
        _render_fetch_progress(
            fetch_title,
            progress_total,
            progress_total,
            footer,
            t("fetch_complete").format(len(all_product_ids), 0),
        )
        return True

    existing_ids_in_file = set()
    try:
        with open(ids_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    existing_ids_in_file.add(line)
    except FileNotFoundError:
        pass
    existing_ids_in_file.update(already_fetched)

    batches = []
    for i in range(0, len(pending_ids), detail_batch_size):
        batches.append(pending_ids[i : i + detail_batch_size])
    total_batches = len(batches)

    info_count = 0
    # Add details progress on top of page progress.
    # Keep the denominator stable based on the estimate from page 0 to avoid jumps.
    progress_done = len(existing_pages)
    _render_fetch_progress(fetch_title, progress_done, progress_total, footer)

    with (
        open(info_path, "a", encoding="utf-8") as f_info,
        open(ids_path, "a", encoding="utf-8") as f_ids,
    ):
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_fetch_detail_batch_task, config, batch, idx + 1): idx + 1
                for idx, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                batch_num = futures[future]
                try:
                    _, products = future.result()
                    with _file_lock:
                        for product in products:
                            f_info.write(json.dumps(product, ensure_ascii=False) + "\n")
                            pid = str(product["id"])
                            if pid not in existing_ids_in_file:
                                f_ids.write(pid + "\n")
                                existing_ids_in_file.add(pid)
                        f_info.flush()
                        f_ids.flush()
                    info_count += len(products)
                    progress_done += 1
                    _render_fetch_progress(fetch_title, progress_done, progress_total, footer)
                except requests.RequestException as e:
                    progress_done += 1
                    _render_fetch_progress(fetch_title, progress_done, progress_total, footer)

    final_detail_count = len(already_fetched) + info_count
    failed_details = len(pending_ids) - info_count
    # Keep the completion line simple and aligned with UI request.
    _render_fetch_progress(
        fetch_title,
        progress_total,
        progress_total,
        footer,
        t("fetch_complete").format(final_detail_count, failed_details),
    )
    return True


def account_settings_menu(config, config_path="config.json"):
    config = normalize_config(config)

    while True:
        config = normalize_config(config)
        accounts = config.get("accounts", [])
        active = config.get("active_account")

        clear_view()
        switch_label = t("account_switch")
        if len(accounts) > 1:
            switch_label = f"{switch_label} ({active})"
        render_dialog(
            t("account_settings_title"),
            [
                switch_label,
                t("account_enter_cookie"),
            ],
            t("account_settings_choose").strip(),
        )
        raw = input().strip()
        if raw == "":
            return config
        if raw == "2":
            clear_view()
            render_dialog(
                t("enter_cookie_title").format(active),
                [
                    t("enter_cookie_help_1"),
                    t("enter_cookie_help_2"),
                    t("enter_cookie_help_3"),
                ],
                t("enter_cookie").strip(),
            )
            cookie = input().strip()
            if cookie:
                save_active_account_cookie(config, cookie, path=config_path)
                config = load_config(config_path)
                print()
                print(t("cookie_saved").format(active))
                input(t("press_enter_continue_prompt"))
            continue
        if raw != "1":
            print(t("invalid_account_choice"))
            print()
            input()
            continue

        while True:
            config = normalize_config(config)
            accounts = config.get("accounts", [])
            active = config.get("active_account")

            clear_view()
            w = len(str(len(accounts)))
            account_lines = []
            for i, acc in enumerate(accounts, start=1):
                name = acc.get("name", "")
                tag = " (active)" if name == active else ""
                account_lines.append(f"  {i:>{w}}. {name}{tag}")

            render_dialog(t("switch_account_title"), account_lines, t("account_choose").strip())
            raw = input().strip()
            if raw == "":
                break
            if not raw.isdigit():
                print(t("invalid_account_choice"))
                print()
                input()
                continue

            idx = int(raw)
            if idx < 1 or idx > len(accounts):
                print(t("invalid_account_choice"))
                print()
                input()
                continue

            selected_name = accounts[idx - 1].get("name", "")
            if selected_name and selected_name != active:
                config["active_account"] = selected_name
                save_active_account(selected_name, path=config_path)
                config = load_config(config_path)
            break


def main():
    config_path = "config.json"

    while True:
        config = load_config(config_path)
        accounts = config.get("accounts", [])
        has_account_settings = len(accounts) >= 1

        clear_view()
        menu_lines = []
        if has_account_settings:
            menu_lines.append(t("menu_0"))
        menu_lines.extend([t("menu_1"), t("menu_2"), t("menu_3")])
        render_dialog(t("title"), menu_lines, (t("choose_multi") if has_account_settings else t("choose")).strip())
        choice = input().strip()

        # Re-read config before executing actions so runtime edits are respected.
        config = load_config(config_path)

        if has_account_settings and choice == "1":
            config = account_settings_menu(config, config_path=config_path)
        elif choice == ("2" if has_account_settings else "1"):
            search_assets_by_query(config)
            input()
        elif choice == ("3" if has_account_settings else "2"):
            clear_view()
            download_dir, _ = _prepare_download_environment(config)
            render_dialog(
                t("download_title"),
                [
                    t("download_dir").format(_display_download_dir(download_dir)),
                    t("download_description"),
                ],
                t("enter_asset_id").strip(),
            )
            raw = input().strip()
            if raw == ".":
                _open_folder(download_dir)
            elif raw:
                download_single_by_id(config, raw)
            if raw:
                print()
                input()
        elif choice == ("4" if has_account_settings else "3"):
            should_pause = extract_assets_menu(config)
            if should_pause:
                print()
                input()
        else:
            print(t("invalid_choice"))
            print()
            input()


# endregion

if __name__ == "__main__":
    main()

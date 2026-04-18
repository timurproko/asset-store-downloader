import json
import math
import os
import re
import subprocess
import sys
import threading
import time
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

# region Library Fetch

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
        cookie = str(acc.get("cookie") or "")
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
    match = re.search(r"_csrf=([^;]+)", cookie_str)
    return match.group(1) if match else ""


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


def print_progress(downloaded, total_size, speed, finished=False):
    if total_size and total_size > 0:
        if finished and downloaded < total_size:
            downloaded = total_size
        pct = min(int(downloaded * 100 / total_size) if total_size else 0, 100)
        bar_len = 25
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        eta = format_eta((total_size - downloaded) / speed) if speed > 0 else "--:--"
        status = (
            f"{bar} {pct:3d}%  {format_size(downloaded)}/{format_size(total_size)}"
            f"  {format_size(speed)}/s  ETA {eta}"
        )
    else:
        status = t("downloaded_no_total").format(
            format_size(downloaded), format_size(speed)
        )
    with _print_lock:
        if finished:
            print(status)
        else:
            print(status, end="\r", flush=True)


def _print_bar_progress(done, total, label, finished=False):
    total = int(total or 0)
    done = int(done or 0)
    if total <= 0:
        pct = 0
    else:
        pct = min(int(done * 100 / total), 100)
    bar_len = 25
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    with _print_lock:
        status = f"{bar} {pct:3d}%  {done}/{total}"
        if finished:
            print(status)
        else:
            print(status, end="\r", flush=True)


def parse_filename(response, asset_id):
    cd = response.headers.get("content-disposition", "")
    match = re.search(r'filename="(.+?)"', cd)
    if match:
        return unquote(match.group(1))
    match = re.search(r"filename\*=UTF-8''(.+)", cd)
    if match:
        return unquote(match.group(1))
    return f"{asset_id}.unitypackage"


def download_asset(asset_id, config, download_dir, total_size=0):
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
                filename = parse_filename(resp, asset_id)
                filepath = download_dir / filename
                if tmp_path and tmp_path.exists():
                    tmp_path.rename(filepath)
                    return asset_id, True, t("resume_full").format(filename)

            resp.raise_for_status()

            filename = parse_filename(resp, asset_id)
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
                    print_progress(downloaded, effective_total, speed)

            elapsed = time.time() - start_time
            speed = (downloaded - resumed_bytes) / elapsed if elapsed > 0 else 0
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

        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            cached_filename = meta.get("filename", "")
            if cached_filename:
                filepath = download_dir / cached_filename
                if filepath.exists():
                    skipped.append((aid, cached_filename))
                    continue

        product_name = info.get("name", "")
        if product_name:
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
    name = (info_map.get(asset_id, {}).get("name") or "").strip()
    if name:
        return f"{name}.unitypackage"
    return f"{asset_id}.unitypackage"


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


def list_assets_id_name(config=None):
    if config is None:
        config = load_config()
    _, info_path, _ = account_data_paths(config)
    info_map = load_info_map(info_path)
    if not info_map:
        print(t("no_asset_info"))
        return

    def sort_key(pid):
        try:
            return (0, int(pid))
        except ValueError:
            return (1, pid)

    ids_sorted = sorted(info_map.keys(), key=sort_key)
    id_width = max(len(str(pid)) for pid in ids_sorted)
    for pid in ids_sorted:
        name = info_map[pid].get("name", "")
        print(f"  {pid:>{id_width}}  {name}")


def search_assets_by_query(config):
    _, info_path, _ = account_data_paths(config)
    info_map = load_info_map(info_path)
    if not info_map:
        ok = run_fetch_list(config)
        if not ok:
            print(t("no_asset_info"))
            return
        info_map = load_info_map(info_path)
        if not info_map:
            print(t("no_asset_info"))
            return

    def sort_key(pid):
        try:
            return (0, int(pid))
        except ValueError:
            return (1, pid)

    while True:
        raw = input(t("enter_search_query")).strip()
        if raw == ".":
            run_fetch_list(config)
            info_map = load_info_map(info_path)
            if not info_map:
                print(t("no_asset_info"))
            continue
        if not raw:
            print()
            list_assets_id_name(config)
            return

        needle = raw.lower()

        matched = []
        for pid, info in info_map.items():
            name = (info.get("name") or "").lower()
            pid_str = str(pid)
            if needle in name or needle in pid_str.lower():
                matched.append(pid)

        matched.sort(key=sort_key)
        if not matched:
            print()
            print(t("search_no_results"))
            return

        print()
        id_width = max(len(str(pid)) for pid in matched)
        for pid in matched:
            name = info_map[pid].get("name", "")
            print(f"  {pid:>{id_width}}  {name}")
        return


def download_single_by_id(config, asset_id_str):
    asset_id_str = asset_id_str.strip()
    if not asset_id_str:
        return
    if not asset_id_str.isdigit():
        return

    print()

    download_dir, cache_dir = _prepare_download_environment(config)
    _, info_path, _ = account_data_paths(config)
    info_map = load_info_map(info_path)
    skipped, pending_ids = _pre_check_downloads(
        [asset_id_str], download_dir, cache_dir, info_map
    )
    print(t("download_dir").format(_display_download_dir(download_dir)))

    if skipped:
        for aid, fname in skipped:
            with _print_lock:
                print(t("exists_skip").format(fname))
        return

    if not pending_ids:
        return

    aid = pending_ids[0]
    print(t("pending_download").format(_pending_display_filename(aid, info_map)))
    asset_id, ok, msg = download_asset(
        aid,
        config,
        download_dir,
        info_map.get(aid, {}).get("size", 0),
    )
    with _print_lock:
        if not ok:
            print(msg)
    print(t("download_done").format(1 if ok else 0, 0 if ok else 1))


def _extract_unitypackage_with_progress(package_path, output_path, encoding="utf-8"):
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

        for i, (asset_entry_dir, asset_out_path) in enumerate(items, start=1):
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
            dest_dir = os.path.dirname(asset_out_path)
            if dest_dir:
                os.makedirs(dest_dir, exist_ok=True)
            shutil.move(os.path.join(asset_entry_dir, "asset"), asset_out_path)
        with _print_lock:
            print()
        return total


def extract_assets_menu(config):
    print()
    download_dir, _ = _prepare_download_environment(config)
    download_dir = download_dir.resolve()
    extract_root = download_dir.parent / "extracts"

    packages = sorted(
        download_dir.glob("*.unitypackage"),
        key=lambda p: p.name.lower(),
    )
    if not packages:
        print(t("no_unitypackages"))

    else:
        w = len(str(len(packages)))
        for i, p in enumerate(packages, start=1):
            print(f"  {i:>{w}}  {p.name}")

    print()
    raw = input(t("enter_extract_index")).strip()
    if raw == "":
        return
    if raw == ".":
        _open_folder(extract_root)
        return
    if not packages:
        print(t("invalid_extract_index"))
        return
    if not raw.isdigit():
        print(t("invalid_extract_index"))
        return
    n = int(raw)
    if n < 1 or n > len(packages):
        print(t("invalid_extract_index"))
        return

    package_path = packages[n - 1]
    extract_root.mkdir(parents=True, exist_ok=True)
    out_dir = extract_root / package_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    print()
    try:
        print(t("extraction_dir").format(_display_download_dir(extract_root)))
        print(t("pending_download").format(package_path.name))
        count = _extract_unitypackage_with_progress(str(package_path), str(out_dir))
        print(t("extract_complete").format(count, 0))
    except ImportError:
        print(t("extractor_missing"))
        print(t("extract_complete").format(0, 1))
    except Exception as e:
        print(t("extract_failed").format(e))
        print(t("extract_complete").format(0, 1))

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


def run_fetch_list(config, detail_batch_size=100):
    page_size = 100
    list_path, info_path, ids_path = account_data_paths(config)
    max_workers = config.get("max_workers", 3)
    _file_lock = threading.Lock()

    account_name = str(normalize_config(config).get("active_account") or "").strip() or "Account"
    print()
    print(t("fetching_assets").format(account_name))

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
    _print_bar_progress(progress_done, progress_total, "", finished=False)

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
                        _print_bar_progress(progress_done, progress_total, "", finished=False)
                    except requests.RequestException as e:
                        _print_bar_progress(progress_done, progress_total, "", finished=False)

        still_missing = [p for p in range(total_pages) if p not in existing_pages]
        if still_missing:
            _print_bar_progress(len(existing_pages), progress_total, "", finished=True)
            print(t("fetch_complete").format(len(existing_pages), len(still_missing)))
            print(t("still_missing").format(len(still_missing), still_missing))
            print(t("rerun"))
            return False

    all_product_ids = extract_product_ids_from_list(existing_pages)
    already_fetched = load_existing_detail_ids(info_path)
    pending_ids = [pid for pid in all_product_ids if pid not in already_fetched]

    if not pending_ids:
        _print_bar_progress(progress_total, progress_total, "", finished=True)
        print(t("fetch_complete").format(len(all_product_ids), 0))
        print()
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
    _print_bar_progress(progress_done, progress_total, "", finished=False)

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
                    _print_bar_progress(progress_done, progress_total, "", finished=False)
                except requests.RequestException as e:
                    progress_done += 1
                    _print_bar_progress(progress_done, progress_total, "", finished=False)

    final_detail_count = len(already_fetched) + info_count
    _print_bar_progress(progress_total, progress_total, "", finished=True)
    failed_details = len(pending_ids) - info_count
    # Keep the completion line simple and aligned with UI request.
    print(t("fetch_complete").format(final_detail_count, failed_details))
    print()
    return True


def account_settings_menu(config, config_path="config.json"):
    config = normalize_config(config)
    accounts = config.get("accounts", [])
    if len(accounts) < 2:
        return config

    print()
    print(t("account_settings_title"))
    print("=" * 40)

    active = config.get("active_account")
    w = len(str(len(accounts)))
    for i, acc in enumerate(accounts, start=1):
        name = acc.get("name", "")
        tag = " (active)" if name == active else ""
        print(f"  {i:>{w}}. {name}{tag}")

    print()
    raw = input(t("account_choose")).strip()
    if raw == "":
        return config
    if not raw.isdigit():
        print(t("invalid_account_choice"))
        print()
        return config

    idx = int(raw)
    if idx < 1 or idx > len(accounts):
        print(t("invalid_account_choice"))
        print()
        return config

    selected_name = accounts[idx - 1].get("name", "")
    if selected_name and selected_name != active:
        config["active_account"] = selected_name
        save_active_account(selected_name, path=config_path)
    return normalize_config(config)


def main():
    config_path = "config.json"

    while True:
        config = load_config(config_path)
        accounts = config.get("accounts", [])
        has_account_settings = len(accounts) >= 2

        print(t("title"))
        print("=" * 40)
        if has_account_settings:
            print(t("menu_0"))
        print(t("menu_1"))
        print(t("menu_2"))
        print(t("menu_3"))
        print("=" * 40)

        choice = input(t("choose_multi") if has_account_settings else t("choose")).strip()

        # Re-read config before executing actions so runtime edits are respected.
        config = load_config(config_path)

        if has_account_settings and choice == "0":
            config = account_settings_menu(config, config_path=config_path)
        elif choice == "1":
            search_assets_by_query(config)
            print()
        elif choice == "2":
            raw = input(t("enter_asset_id")).strip()
            if raw == ".":
                download_dir, _ = _prepare_download_environment(config)
                _open_folder(download_dir)
            elif raw:
                download_single_by_id(config, raw)
            print()
        elif choice == "3":
            extract_assets_menu(config)
            print()
        else:
            print(t("invalid_choice"))
            print()


# endregion

if __name__ == "__main__":
    main()

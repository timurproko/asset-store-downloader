# Unity Asset Store Downloader

Download purchased assets from the [Unity Asset Store](https://assetstore.unity.com). English-only CLI with three actions: **Search** (IDs and names), **Download** (`.unitypackage` by product ID), and **Extract** (unpack a downloaded package).

## Features

- **Search assets** — Filters rows from `asset_info.jsonl` by substring in the product **name** or **ID** (case-insensitive). **Enter = full list** (empty query lists everything). If there is no local detail data yet, the app runs a **full library fetch** (list + product details) first, then asks for the search string. Prompt: `Enter search query (Enter = full list):`
- **Download assets** — Asks for a numeric **asset ID** and downloads one `.unitypackage` into `download_dir`. Shows `Download dir:`, then `Asset:` plus the display filename (product name from `asset_info.jsonl` plus `.unitypackage`, or `{id}.unitypackage` if the name is missing), a one-line progress bar, and `Download complete: …`. Skips if the file is already present (`Exists, skipped: …`). Prompt: `Enter asset ID (Enter = cancel, . = open):` — **`.`** opens `download_dir` in the system file manager.  
- **Extract assets** — Lists `*.unitypackage` files under `download_dir` with a numeric **index** (1…N). Extraction uses **`tarsafe`** to read the package (same on-disk format as the [unitypackage-extractor](https://pypi.org/project/unitypackage-extractor/) project) but implemented in-app: one **progress line** (`Extracting i/total (pct%)`) and a single result line `Extracted N file(s) to: <path>`. Output goes to **`extracted/<package-stem>/`** next to **`downloads/`** (sibling folders). Prompt: `Enter asset index (Enter = cancel, . = open):` — **`.`** opens the **`extracted/`** folder (next to `download_dir`). If there are no packages, you still get this prompt so you can open **`extracted/`**.
- **Menu** — Repeats after each action. **Ctrl+C** exits; there is no separate “quit” command.
- **Resume / retry** — Downloads can resume via `.tmp` files and `Range` requests; fetches skip existing JSONL rows; HTTP errors retry with backoff.

## Requirements

```bash
pip install -r requirements.txt
```

Installs **`requests`** (API and downloads) and **`unitypackage-extractor`** as declared on PyPI (it pulls in **`tarsafe`**, which **Extract assets** imports). If extraction fails with a missing-module message, run the same install command inside your environment (e.g. WSL).

## Setup

1. Copy the example config:
   ```bash
   cp config.json.example config.json
   ```
2. Log in to the Asset Store in your browser.
3. Open DevTools (F12) → **Network** → copy the `Cookie` header from a request to `assetstore.unity.com`.
4. Paste it into `config.json`:

![](pics/cookie.png)

```json
{
  "cookie": "your_cookie_string_here",
  "download_dir": "./downloaded",
  "max_workers": 3,
  "retry": 3,
  "timeout": 300
}
```

| Field | Description |
| --- | --- |
| `cookie` | Full cookie string from the browser |
| `download_dir` | Folder for `.unitypackage` files (default `./downloaded`) |
| `max_workers` | Parallel workers for list/detail fetch (typical: `3`) |
| `retry` | Retries per failed HTTP request |
| `timeout` | Request timeout in seconds |

Paths are resolved from the current working directory unless you use an absolute `download_dir`.

## Usage

```bash
python asset_store_download.py
```

| # | Action |
| --- | --- |
| **1** | **Search assets** — Fetch library data if needed, then `Enter search query (Enter = full list):` — matching IDs/names, or full list if you press Enter only. |
| **2** | **Download assets** — `Enter asset ID (Enter = cancel, . = open):` — download one package, or **`.`** to open `download_dir`. |
| **3** | **Extract assets** — List packages in `download_dir`, then `Enter asset index (Enter = cancel, . = open):` — unpack into `extracted/…`, or **`.`** to open the `extracted/` folder. |

Wrong menu choice shows `Invalid choice` (with a blank line before the menu repeats). For **Download assets**, `Enter` alone cancels; other non-numeric input (except **`.`**) returns to the menu without a message.

### Windows: `start.bat`

[`start.bat`](start.bat) resolves the repo path for **WSL** and runs `python3 asset_store_download.py`. WSL must be available (`wsl --status`). Install dependencies in that distro (`pip install -r requirements.txt`). The window stays open at the end (`pause`). If **`xdg-open`** is not installed, **`.`** (open folder) still works by launching **Windows Explorer** for the path (`wslpath` + `explorer.exe`); optional: `sudo apt install -y xdg-utils` for a Linux file manager.

## Output layout

| Path | Role |
| --- | --- |
| `asset_list.jsonl` | Paginated `searchMyAssets` responses (with `page`) |
| `asset_info.jsonl` | One product JSON per line (used for search) |
| `asset_ids.txt` | IDs appended while details are fetched |
| `<download_dir>/` | `.unitypackage` files and `<download_dir>/.cache/` (resume metadata) |
| `<download_dir>/../extracted/<name>/` | Unpacked contents (same parent as `download_dir`; default layout: `downloaded/` and `extracted/` side by side) |

## Resume behavior

- **Fetch**: Skips pages and product IDs already present in the JSONL files.
- **Download**: Resumes partial `.tmp` downloads using `Range` when supported.

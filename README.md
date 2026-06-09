# Unity asset store downloader

Download purchased assets from the [Unity Asset Store](https://assetstore.unity.com). English-only CLI with account settings plus three actions: **Search** (IDs and names), **Download** (`.unitypackage` by product ID), and **Extract** (unpack a downloaded package).

## Features

- **Search assets** — Automatically fetches the latest library data first, then filters rows from `asset_info.jsonl` by substring in the product **name** or **ID** (case-insensitive). **Enter = full list** (empty query lists everything). Footer: `Enter = full list`.
- **Download assets** — Asks for a numeric **asset ID** and downloads one `.unitypackage` into `download_dir`. Shows `Directory:`, then `Asset:` plus the display filename (product name from `asset_info.jsonl` plus `.unitypackage`, or `{id}.unitypackage` if the name is missing), a one-line progress bar, and `Download complete: …`. Skips if the file is already present (`File exists, downloading skipped`). Prompt: `Enter asset ID (Enter = cancel, . = open):` — **`.`** opens `download_dir` in the system file manager.  
- **Extract assets** — Lists `*.unitypackage` files under `download_dir` with a numeric **index** (1…N). Extraction uses **`tarsafe`** to read the package (same on-disk format as the [unitypackage-extractor](https://pypi.org/project/unitypackage-extractor/) project) but implemented in-app: one **progress line** (`Extracting i/total (pct%)`) and a single result line `Extracted N file(s) to: <path>`. Output goes to **`extracts/<package-stem>/`** next to **`downloads/`** (sibling folders). Prompt: `Enter asset index (Enter = back, . = open):` — **`.`** opens the **`extracts/`** folder (next to `download_dir`). If there are no packages, you still get this prompt so you can open **`extracts/`**.
- **Extract assets** — Lists `*.unitypackage` files under `download_dir` with a numeric **index** (1…N). Extraction uses **`tarsafe`** to read the package (same on-disk format as the [unitypackage-extractor](https://pypi.org/project/unitypackage-extractor/) project) but implemented in-app: one **progress line** (`Extracting i/total (pct%)`) and a single result line `Extracted N file(s) to: <path>`. Output goes to **`extracts/<package-stem>/`** next to **`downloads/`** (sibling folders). Prompt: `Enter asset index (Enter = back, . = open):` — **`.`** opens the **`extracts/`** folder (next to `download_dir`). If there are no packages, you still get this prompt so you can open **`extracts/`**.
- **Account settings** — Switch the active account or choose **2. Enter cookie** to paste a new cookie header for the currently active account. The updated cookie is saved to `config.json`.
- **Menu** — Repeats after each action. **Ctrl+C = exit**; there is no separate “quit” command.
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
  "accounts": [
    { "name": "Personal", "download_dir": "./downloads/personal", "cookie": "your_cookie_string_here" }
  ],
  "active_account": "Personal",
  "max_workers": 3,
  "retry": 3,
  "timeout": 300
}
```

| Field | Description |
| --- | --- |
| `accounts` | List of accounts (`name` + `cookie`) |
| `active_account` | Which account name is currently active |
| `accounts[].download_dir` | Folder for `.unitypackage` files for that account (default `./downloads`) |
| `max_workers` | Parallel workers for list/detail fetch (typical: `3`) |
| `retry` | Retries per failed HTTP request |
| `timeout` | Request timeout in seconds |

### Multiple accounts

To add multiple cookies, add more entries to `accounts`. The main menu shows **1. Account settings** where you can switch the active account; the selection is saved back to `config.json`. In Account settings, choose **2. Enter cookie** to paste a cookie header for the currently active account and save it to `config.json`.

Legacy configs that only have a top-level `cookie` field are still supported (treated as a single account).

Paths are resolved from the current working directory unless you use an absolute `download_dir`.

## Usage

```bash
python asset_store_download.py
```

| # | Action |
| --- | --- |
| **1** | **Account settings** — Switch accounts or choose **2. Enter cookie** to save a pasted cookie header to the active account in `config.json`. |
| **2** | **Search assets** — Fetch library data automatically, then `Enter = full list` — matching IDs/names, or full list if you press Enter only. |
| **3** | **Download assets** — `Enter asset ID (Enter = cancel, . = open):` — download one package, or **`.`** to open `download_dir`. |
| **4** | **Extract assets** — List packages in `download_dir`, then `Enter asset index (Enter = back, . = open):` — unpack into `extracts/…`, or **`.`** to open the `extracts/` folder. |

Wrong menu choice shows `Invalid choice` (with a blank line before the menu repeats). For **Download assets**, `Enter` alone cancels; other non-numeric input (except **`.`**) returns to the menu without a message.

### Windows: `start.bat`

[`start.bat`](start.bat) resolves the repo path for **WSL** and runs `python3 asset_store_download.py`. WSL must be available (`wsl --status`). Install dependencies in that distro (`pip install -r requirements.txt`). The window stays open at the end (`pause`). If **`xdg-open`** is not installed, **`.`** (open folder) still works by launching **Windows Explorer** for the path (`wslpath` + `explorer.exe`); optional: `sudo apt install -y xdg-utils` for a Linux file manager.

## Output layout

| Path | Role |
| --- | --- |
| `data/asset_list.<account>.jsonl` | Paginated `searchMyAssets` responses (with `page`) per account |
| `data/asset_info.<account>.jsonl` | One product JSON per line (used for search) per account |
| `data/asset_ids.<account>.txt` | IDs appended while details are fetched per account |
| `<download_dir>/` | `.unitypackage` files and `<download_dir>/.cache/` (resume metadata) |
| `<download_dir>/../extracts/<name>/` | Unpacked contents (same parent as `download_dir`; default layout: `downloads/` and `extracts/` side by side) |

## Resume behavior

- **Fetch**: Skips pages and product IDs already present in the JSONL files.
- **Download**: Resumes partial `.tmp` downloads using `Range` when supported.

# Unity Asset Store Downloader

Download purchased assets from the [Unity Asset Store](https://assetstore.unity.com). **Search** shows **ID and name** in the console (aligned columns), with optional filter or full list; **Download assets** saves **`.unitypackage`** files; **Extract assets** unpacks a downloaded package using [unitypackage-extractor](https://pypi.org/project/unitypackage-extractor/). The interface is **English only**.

## Features

- **Search assets** — Enter a search string to show a **filtered** list (matches product name or ID substring, case-insensitive). **Leave the search empty** to print the **full** list. If `asset_info.jsonl` is empty, the app **fetches your library** from the store first (same as the former “list” step), then prompts for search.
- **Download assets** — Downloads one package into `download_dir` using the product ID (no prior fetch required if you already know the ID).
- **Extract assets** — Lists `.unitypackage` files in `download_dir`, pick by **number**, contents go to **`extracted/<package name>/`** beside `downloads/` (same parent folder) via [unitypackage-extractor](https://pypi.org/project/unitypackage-extractor/).
- **Menu loop** — After each action, the main menu is shown again. Exit with **Ctrl+C** (there is no quit item on the menu).
- **Resume** — Interrupted downloads continue from the last byte (`.tmp` + `Range` requests).
- **Progress** — Progress bar, speed, and ETA when size is known.
- **Incremental fetch** — When the library is fetched, already stored pages and details in JSONL files are skipped.
- **Auto retry** — Server and network errors retry with backoff.

## Requirements

```bash
pip install -r requirements.txt
```

Installs `requests` and [`unitypackage-extractor`](https://pypi.org/project/unitypackage-extractor/) (only needed for **Extract assets**).

## Setup

1. Copy the example config:
   ```bash
   cp config.json.example config.json
   ```
2. Log in to the Asset Store in your browser.
3. Open DevTools (F12) → **Network** → copy the `Cookie` header from any request to `assetstore.unity.com`.
4. Paste it into `config.json`:

![](pics/cookie.png)

```json
{
  "cookie": "your_cookie_string_here",
  "download_dir": "./downloads",
  "max_workers": 3,
  "retry": 3,
  "timeout": 300
}
```

| Field | Description |
| --- | --- |
| `cookie` | Full cookie string from the browser |
| `download_dir` | Where `.unitypackage` files are saved |
| `max_workers` | Parallelism for list/detail fetch (recommended: 3) |
| `retry` | Retries per failed HTTP request |
| `timeout` | Request timeout in seconds |

## Usage

```bash
python asset_store_download.py
```

You get a repeating menu:

| # | Action |
| --- | --- |
| **1** | **Search assets** — If needed, fetches library data, then prompts for a query; prints matching IDs and names (empty input = full list), then returns to the menu. |
| **2** | **Download assets** — Prompts for a product ID and downloads that `.unitypackage`. |
| **3** | **Extract assets** — Lists packages in `download_dir` with numbers; enter a number to extract into `extracted/…` next to `downloads/` (Enter = cancel). |

Invalid input prints *Invalid choice* and shows the menu again.

### Windows: `start.bat`

[`start.bat`](start.bat) converts the project folder to a WSL path and runs:

`python3 asset_store_download.py`

inside your default WSL distro. **WSL must be installed** (`wsl --status`). If Python or dependencies are missing inside WSL, run `pip install -r requirements.txt` there (the script prints a hint on failure). The window stays open at the end (`pause`).

## Output files

| File | Description |
| --- | --- |
| `asset_list.jsonl` | One JSON object per line: each page of `searchMyAssets` data (includes `page`) |
| `asset_info.jsonl` | One JSON object per line: full product detail from the API |
| `asset_ids.txt` | Product IDs appended during detail fetch (for your own reference) |
| `downloads/` | Downloaded `.unitypackage` files (and `downloads/.cache/` for resume metadata) |
| `extracted/` | Next to `downloads/`: one subfolder per extracted package (same level as the `downloads` folder) |

## Resume behavior

- **List / details**: Existing `asset_list.jsonl` / `asset_info.jsonl` rows are skipped; only missing pages or product IDs are fetched.
- **Download**: Partial files use a `.tmp` suffix; the client sends `Range` to continue.

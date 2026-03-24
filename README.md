# Unity Asset Store Batch Downloader

Download purchased assets from the [Unity Asset Store](https://assetstore.unity.com). The app fetches your library list and product details, prints **ID and name** in the console (aligned columns), and can download a **`.unitypackage`** by numeric product ID. The interface is **English only**.

## Features

- **List assets** — Fetches your asset list and product details via the GraphQL API (paginated), writes JSONL files, then prints every asset **ID and name**.
- **Download by ID** — Downloads one package into `download_dir` using the product ID (no prior fetch required if you already know the ID).
- **Menu loop** — After **List assets**, you are prompted to **press any key** to return to the menu. **Download by ID** returns to the menu when the download step finishes. Exit with **Ctrl+C** (there is no quit item on the menu).
- **Resume** — Interrupted downloads continue from the last byte (`.tmp` + `Range` requests).
- **Progress** — Progress bar, speed, and ETA when size is known.
- **Incremental fetch** — Re-running skips pages and product details already stored in JSONL files.
- **Auto retry** — Server and network errors retry with backoff.

## Requirements

```bash
pip install requests
```

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
| **1** | **List assets** — Full fetch (list + details), then print IDs and names. Afterwards: *Press any key to return to menu…* |
| **2** | **Download by ID** — Prompts for a product ID and downloads that `.unitypackage`. |

Invalid input prints *Invalid choice* and shows the menu again.

### Windows: `start.bat`

[`start.bat`](start.bat) converts the project folder to a WSL path and runs:

`python3 asset_store_download.py`

inside your default WSL distro. **WSL must be installed** (`wsl --status`). If Python or `requests` is missing inside WSL, install them there (the script prints a hint on failure). The window stays open at the end (`pause`).

## Output files

| File | Description |
| --- | --- |
| `asset_list.jsonl` | One JSON object per line: each page of `searchMyAssets` data (includes `page`) |
| `asset_info.jsonl` | One JSON object per line: full product detail from the API |
| `asset_ids.txt` | Product IDs appended during detail fetch (for your own reference) |
| `downloads/` | Downloaded `.unitypackage` files (and `downloads/.cache/` for resume metadata) |

## Resume behavior

- **List / details**: Existing `asset_list.jsonl` / `asset_info.jsonl` rows are skipped; only missing pages or product IDs are fetched.
- **Download**: Partial files use a `.tmp` suffix; the client sends `Range` to continue.

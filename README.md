# QScreen Filing Tool

Turn a PDF financial report into a QSE-format filing JSON — lossless, auditable, and ready to upload.
Two modes: **local browser app** (drag-and-drop) or **one-command CLI**.

## Install

**Option 1 — installer script** (clones to `~/.qscreen-filing-tool`, installs deps, self-tests):

```bash
curl -fsSL https://raw.githubusercontent.com/0xBingBong69/qscreen-filing-tool/main/install.sh | bash
```

Re-run anytime to update.

**Option 2 — pip** (installs the `qscreen-ingest` and `qscreen-app` commands):

```bash
pip install -e .                 # core
pip install -e ".[xlsx,ocr]"     # + Excel export and OCR for scanned PDFs
```

## Configure (once)

Create a `.env` next to the tool (it is gitignored):

```
OPENROUTER_API_KEY=sk-or-...        # LLM key (or MINIMAX_API_KEY / LLM_API_KEY)
INGEST_TOKEN=...                    # qscreen.app ingest token (only needed to upload)
QSCREEN_API_URL=https://qscreen.app # defaults to http://localhost:3004
```

> **Note on Claude Code on the web:** the managed environment's network policy
> may block LLM providers (e.g. `openrouter.ai`). The extractor needs to reach
> the provider, so run it where that host is allowed, or permit it in the
> environment's network policy.

## Option A — local browser app

```bash
python3 qscreen_app.py            # or: qscreen-app
```

Open **http://127.0.0.1:8765**, drag in a PDF, fill Symbol / Sector / Year /
Period (type a known symbol and the sub-sector auto-fills), click **Extract**.
When it finishes, click **Download** to get the `SYMBOL_YEAR_PERIOD_filing.json`.
Nothing is auto-uploaded — you stay in control. An **Upload to qscreen.app**
button appears only when the server has `INGEST_TOKEN` set, and only uploads
when you click it. An *Advanced* panel lets you pick a different provider/model.

## Option B — CLI (per PDF)

```bash
python3 qscreen_ingest.py <PDF_PATH> \
  --symbol QIBK --sector islamic_bank --year 2024 --period FY
```

- `--sector`: `conventional_bank | islamic_bank | industrial | insurance | other`
- `--period`: `FY | Q1 | Q2 | Q3 | Q4 | H1 | 9M` (default `FY`)
- `--dry-run` — produce the JSON **without** uploading (inspect first)
- `--export csv` / `--export xlsx` — also write a flattened line-items table (repeatable)
- `--ocr auto|never|always` — OCR scanned pages (`auto` only does near-empty pages; needs the `ocr` extra + system `tesseract`/`poppler`)
- `--version` — print the tool version

The tool extracts (chunked page windows + table recovery), normalizes the
fields to the contract, validates, saves `SYMBOL_YEAR_PERIOD_filing.json`, and
uploads to qscreen.app. A non-conforming extract is saved but **not** uploaded.

### Batch mode

Process many filings from a CSV manifest (`pdf,symbol,sector,year[,period]`):

```bash
python3 qscreen_ingest.py --manifest filings.csv --export csv
```

```csv
pdf,symbol,sector,year,period
reports/QIBK_2024.pdf,QIBK,islamic_bank,2024,FY
reports/QNBK_2023.pdf,QNBK,conventional_bank,2023,FY
```

One bad filing is reported and the batch continues; a summary prints at the end.

## Testing

```bash
python3 qscreen_ingest.py --self-test     # offline contract/normalize/merge check
pytest -q                                 # full suite (pip install -e ".[dev]")
```

The self-test must print `✅ self-test passed`. CI runs both on Python 3.9–3.12.

## License

MIT — see [LICENSE](LICENSE).

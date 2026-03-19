# Redactor

A self-hosted document redaction service that automatically detects and blacks out personally identifiable information (PII) from PDFs and images. Deployed as a Docker stack with a web-based configuration dashboard.

---

## Features

- **Multi-format input** — PDF (text-layer and scanned/image-based), PNG, JPG, TIFF
- **Dual ingestion modes** — REST API upload or automatic folder polling
- **Dual output modes** — save to output directory or deliver via signed webhook
- **Four redaction levels** — Minimal → Standard → Aggressive → Maximum, plus fully custom entity selection
- **Custom profiles** — save named entity sets for reuse across jobs
- **Forensically sound PDF redaction** — removes text from the PDF content stream; extracted text cannot be recovered by copy, select, or forensic tools
- **OCR redaction** — detects and redacts PII in scanned documents and images via Tesseract
- **Webhook templates** — Jinja2 templates that shape the exact JSON payload posted to any endpoint; credentials stored in the template, not per-request
- **Dynamic per-job variables** — pass arbitrary key/value pairs at submit time that are merged into the template context
- **Web dashboard** — job monitoring with inline document preview, configuration, profile management, webhook management
- **Docker deployment** — single `docker compose up` with persistent volumes

---

## Quick Start

```bash
git clone https://github.com/Fybre/redactor
cd redactor
cp .env.example .env
docker compose up -d
```

The web UI is available at **http://localhost:8080** (or the port set in `.env`).

---

## Detection Engine

Redactor uses **pattern matching and statistical NLP** — not a generative AI model.

| Component | Role |
|---|---|
| **Microsoft Presidio** | Orchestration layer; runs recognisers and returns spans |
| **spaCy `en_core_web_lg`** | Named Entity Recognition for persons, organisations, locations — a statistical ML classifier trained on annotated text |
| **Presidio regex recognisers** | Structured identifiers: credit cards, SSNs, IBANs, phone numbers, emails, passports, etc. |
| **Tesseract** | OCR for scanned pages — pure computer vision, no AI |
| **PyMuPDF** | Text extraction and PDF manipulation |

This means detection is fast and deterministic. It can miss PII that doesn't match known patterns, and may occasionally false-positive on ambiguous text. It does not require internet access or an API key.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Docker Network                    │
│                                                      │
│  ┌──────────────────┐      ┌──────────────────────┐  │
│  │   frontend       │      │   backend            │  │
│  │   nginx:80       │─────▶│   FastAPI:8000       │  │
│  │                  │      │                      │  │
│  │  Static HTML/JS  │      │  • REST API          │  │
│  │  Reverse proxy   │      │  • Job worker        │  │
│  └──────────────────┘      │  • Folder poller     │  │
│           │                └──────────┬───────────┘  │
└───────────┼───────────────────────────┼──────────────┘
            │                           │
        Browser                  /data volume
                                  ├── input/
                                  ├── output/
                                  ├── originals/
                                  ├── temp/
                                  ├── redactor.db
                                  └── runtime_config.json
```

**Backend** — FastAPI + two background asyncio tasks:
- **Job worker** — polls the database for queued jobs every second, processes them with configurable concurrency
- **Folder poller** — scans `/data/input` at a configurable interval, auto-submits new files as jobs

**Frontend** — static HTML/CSS/JS served by nginx, which also reverse-proxies all `/api/` requests to the backend.

**Storage** — SQLite database for job tracking; files stored on a named Docker volume.

---

## Redaction Pipeline

### Text-layer PDFs

1. Extract words with bounding boxes via PyMuPDF
2. Reconstruct page text, building a character-offset → bounding-box map
3. Run Presidio NLP analysis on full page text
4. For each PII match, locate the corresponding word bounding boxes
5. Apply `add_redact_annot()` + `apply_redactions()` — **removes text from the PDF content stream**, not just paints over it
6. Save with garbage collection to remove orphaned objects

> Text removed this way cannot be recovered by selecting, copying, or forensic PDF analysis tools.

### Scanned PDFs and Images

1. Render each page to a 300 DPI PIL image
2. Run Tesseract OCR to extract words with pixel bounding boxes
3. Reconstruct text with character-offset → pixel-bbox map
4. Run Presidio on the OCR text
5. Draw filled rectangles over PII bounding boxes using PIL
6. For PDFs: replace the page content with the redacted image

### Mixed PDFs

Each page is assessed individually — pages with a real text layer use pipeline 1; image-only pages use pipeline 2. Both can appear in a single output PDF.

---

## Redaction Levels

| Level | Entities Included |
|---|---|
| **Minimal** | Credit cards, CVV¹, SSN, IBAN, bank accounts, passports, NHS, Aadhaar, PAN, TFN, Medicare |
| **Standard** | + Person names, email, phone, driver's licence, IP address, medical licence, ITIN |
| **Aggressive** | + Locations/addresses, dates & times, URLs, nationality/religion/political groups, regional IDs |
| **Maximum** | + Organisations, ages, monetary values, facility names |
| **Custom** | Any combination of entity types, or use a saved named profile |

¹ CVV detection requires LLM or Both detection strategy — it is a no-op with Presidio only.

---

## Configuration

### Environment Variables (`.env`)

| Variable | Default | Description |
|---|---|---|
| `HOST_PORT` | `8080` | Port the web UI is served on |
| `WORKER_CONCURRENCY` | `2` | Simultaneous redaction jobs |
| `FOLDER_POLLING_ENABLED` | `true` | Watch input folder for new files |
| `POLL_INTERVAL_SECONDS` | `15` | How often to check the input folder |
| `MAX_FILE_SIZE_MB` | `100` | Maximum upload size |
| `RETAIN_ORIGINALS` | `true` | Keep copies of original files |
| `RETENTION_DAYS` | `30` | Days to keep original files |
| `DEFAULT_REDACTION_LEVEL` | `standard` | Default level for polled files |
| `DEFAULT_OUTPUT_MODE` | `directory` | `directory` or `webhook` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `ALLOW_HEADER_REVEAL` | `true` | Set to `false` to prevent sensitive webhook template header values (Authorization, tokens, keys) from being sent to the browser. Once saved they can only be replaced, not viewed. |

Runtime settings can also be changed live via the web UI without restarting the container.

### Data Directories

All directories live inside the `redactor-data` Docker volume, mounted at `/data`:

| Path | Purpose |
|---|---|
| `/data/input/` | Drop files here for automatic processing |
| `/data/output/` | Redacted output files |
| `/data/originals/` | Retained originals (if enabled) |
| `/data/redactor.db` | SQLite job database |
| `/data/runtime_config.json` | Live configuration (edited via web UI) |

To access files from the host:

```bash
docker run --rm -v redactor_redactor-data:/data alpine ls /data/output
```

Or mount a host directory instead of the named volume by editing `docker-compose.yml`:

```yaml
volumes:
  - /your/host/path:/data
```

---

## REST API

Base URL: `http://localhost:8080/api/v1`

### Submit a document (async)

```bash
curl -X POST http://localhost:8080/api/v1/jobs/upload \
  -F file=@document.pdf \
  -F level=standard \
  -F output_mode=directory
```

Returns immediately with `{"status": "queued", "job_id": "..."}`. Poll `/jobs/{job_id}` for the result.

### Submit a document (synchronous)

```bash
curl -X POST http://localhost:8080/api/v1/jobs/upload-sync \
  -F file=@document.pdf \
  -F level=standard \
  -F output_mode=webhook \
  -F webhook_url=https://dms.example.com/api/UpdateDocument \
  -F webhook_template=therefore_update_document \
  -F 'webhook_extra={"doc_no": 123}'
```

Blocks until redaction **and** webhook delivery are complete, then returns:
```json
{
  "status": "completed",
  "job_id": "a1b2c3d4-...",
  "filename": "document.pdf",
  "level": "standard",
  "page_count": 3,
  "entities_found": {"PERSON": 2, "CREDIT_CARD": 1},
  "processing_ms": 4821,
  "webhook_sent": true
}
```

Returns HTTP 500 if redaction fails. Accepts identical parameters to `/upload`. Useful when the caller (e.g. a Therefore workflow REST Call task) needs the document updated before the workflow continues.

**Form fields:**

| Field | Required | Values | Description |
|---|---|---|---|
| `file` | Yes | — | The file to redact |
| `level` | No | `minimal` `standard` `aggressive` `maximum` `custom` | Redaction level (default: `standard`) |
| `custom_entities` | If `level=custom` | JSON array | e.g. `["PERSON","EMAIL_ADDRESS"]` |
| `profile_name` | No | string | Use a saved profile (sets `level=custom`) |
| `output_mode` | No | `directory` `webhook` | Where to deliver the result |
| `webhook_url` | If `webhook` mode | URL | Endpoint to POST the completion event to |
| `webhook_template` | No | string | Name of a saved Jinja2 template to render the payload |
| `webhook_include_file` | No | `true` / `false` | Embed redacted file as base64 in the webhook payload |
| `webhook_secret` | No | string | HMAC signing secret |
| `webhook_extra` | No | JSON object | Extra key/value pairs merged into the template context |

**Response:**
```json
{
  "status": "queued",
  "job_id": "a1b2c3d4-...",
  "filename": "document.pdf",
  "level": "standard"
}
```

### Check job status

```bash
curl http://localhost:8080/api/v1/jobs/{job_id}
```

### Download / view redacted file

```bash
# Download
curl -O -J http://localhost:8080/api/v1/jobs/{job_id}/download

# View inline in browser
open http://localhost:8080/api/v1/jobs/{job_id}/view
```

### Other endpoints

```bash
# List jobs (filterable by status)
curl "http://localhost:8080/api/v1/jobs?status=completed&page=1&per_page=20"

# Delete a job
curl -X DELETE http://localhost:8080/api/v1/jobs/{job_id}

# Retry a failed job
curl -X POST http://localhost:8080/api/v1/jobs/{job_id}/retry

# System stats
curl http://localhost:8080/api/v1/stats
```

---

## Webhooks

When output mode is `webhook`, Redactor POSTs a JSON payload to your endpoint on job completion:

```json
{
  "event": "job.completed",
  "job_id": "a1b2c3d4-...",
  "filename": "document.pdf",
  "status": "completed",
  "level": "standard",
  "page_count": 3,
  "entities_found": {"PERSON": 2, "EMAIL_ADDRESS": 1},
  "processing_ms": 1842,
  "completed_at": "2026-03-18T10:00:01Z",
  "download_url": "http://redactor-host/api/v1/jobs/a1b2c3d4-.../download"
}
```

If `webhook_include_file=true`, the payload also includes `file_data` (base64), `file_name`, and `file_size_bytes`.

### Webhook signatures

If a signing secret is configured, each request includes:

```
X-Redactor-Signature: sha256=<hmac-hex>
X-Redactor-Timestamp: <unix-timestamp>
```

Verify in Python:
```python
import hmac, hashlib

def verify(body: bytes, secret: str, sig_header: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header.removeprefix("sha256="))
```

Redactor retries failed deliveries 3 times with exponential backoff.

---

## Webhook Templates

Templates let you control the exact JSON body that Redactor posts to a webhook. They are Jinja2 templates rendered at job completion using job variables as context. This makes it possible to post directly to third-party APIs (like a DMS) without an intermediary adapter.

### Template variables

| Variable | Description |
|---|---|
| `job_id` | Job UUID |
| `filename` | Original filename |
| `stem` | Filename without extension |
| `status` | `completed` or `failed` |
| `level` | Redaction level (`standard`, `aggressive`, etc.) |
| `page_count` | Number of pages processed |
| `entities_found` | Dict of `{entity_type: count}` |
| `total_entities` | Sum of all entity counts |
| `processing_ms` | Processing time in milliseconds |
| `completed_at` | ISO 8601 timestamp (`2026-03-18T16:22:14Z`) |
| `file_data` | Base64-encoded redacted file (if `webhook_include_file=true`) |
| `file_name` | Output filename |
| `file_size_bytes` | Output file size |
| *(any key from `webhook_extra`)* | Merged in from the per-job `webhook_extra` JSON |

Use `{{ completed_at[:10] }}` to get date-only (`2026-03-18`) where required.

### Template headers

Each template has an optional **HTTP Headers** section — a structured key/value editor. Headers stored here are sent with every POST that uses the template, keeping credentials out of per-request parameters.

Sensitive header names (containing words like `authorization`, `token`, `key`, `secret`, `password`) are automatically masked in the editor. A 👁 toggle reveals the value while editing. Set `ALLOW_HEADER_REVEAL=false` in `.env` to disable the reveal toggle entirely — values are then never sent to the browser and can only be replaced.

### Dynamic per-job variables with `webhook_extra`

Pass a JSON object as `webhook_extra` when submitting a job. Every key becomes available in the template:

```bash
curl -X POST http://localhost:8080/api/v1/jobs/upload \
  -F file=@invoice.pdf \
  -F output_mode=webhook \
  -F webhook_url=https://dms.example.com/api/CreateDocument \
  -F webhook_template=therefore_create_document \
  -F webhook_include_file=true \
  -F 'webhook_extra={"category_no": 299}'
```

Template usage: `{{ category_no | default(57) }}`

### Duplicating templates

Use the **Duplicate** button in Configuration → Webhook Templates to copy a template (including its headers). Useful when you need the same auth credentials but different category numbers or endpoint-specific field mappings.

---

## Custom Redaction Profiles

Profiles let you save a named set of entity types for repeated use.

**Create via UI:** Configuration → Redaction Profiles → New Profile

**Create via API:**
```bash
curl -X POST http://localhost:8080/api/v1/config/profiles \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GDPR Essentials",
    "entities": ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "LOCATION"],
    "description": "Core GDPR personal data fields"
  }'
```

**Use a profile on upload:**
```bash
curl -X POST http://localhost:8080/api/v1/jobs/upload \
  -F file=@document.pdf \
  -F profile_name="GDPR Essentials"
```

---

## Therefore™ Integration

Redactor can post directly to Therefore's REST API on job completion using a webhook template — no adapter service required.

### Step 1 — Store credentials in the webhook template

In **Configuration → Webhook Templates**, edit `therefore_create_document` and add your credentials in the **HTTP Headers** field:

```json
{
  "Authorization": "Basic <base64(username:password)>",
  "TenantName": "yourtenantname",
  "Content-Type": "application/json"
}
```

`TenantName` is only required for Therefore Online (`*.thereforeonline.com`). Set it to your subdomain prefix (e.g. `acme` for `acme.thereforeonline.com`).

The default template body posts to `CreateDocument` with `CategoryNo`, index fields, and the redacted file as a base64 stream. Edit the template body to match your category's field definitions.

**Date fields** — Therefore's `DateIndexData` expects `yyyy-MM-dd`. Use `{{ completed_at[:10] }}`:

```json
{
  "DateIndexData": {
    "FieldName": "Redaction_Date",
    "FieldNo": 0,
    "DataISO8601Value": "{{ completed_at[:10] }}"
  }
}
```

### Step 2 — Configure Therefore's outgoing REST Request

In Therefore, set up a REST Request action to POST documents to Redactor. Pass the required fields using Therefore's Body tab:

| Field | Value |
|---|---|
| `level` | `standard` |
| `output_mode` | `webhook` |
| `webhook_url` | `https://your-therefore-url/theservice/v0001/restun/CreateDocument` |
| `webhook_template` | `therefore_create_document` |
| `webhook_include_file` | `true` |
| `webhook_extra` | `{"category_no": 57}` (optional — overrides the default in the template) |

**Equivalent curl for testing:**
```bash
curl -X POST http://redactor-host:8080/api/v1/jobs/upload \
  -F file=@invoice.pdf \
  -F level=standard \
  -F output_mode=webhook \
  -F webhook_url=https://acme.thereforeonline.com/theservice/v0001/restun/CreateDocument \
  -F webhook_template=therefore_create_document \
  -F webhook_include_file=true \
  -F 'webhook_extra={"category_no": 57}'
```

### Notes

- **Field numbers** — `FieldNo` in the template must match your Therefore category. Look them up with `POST /restun/GetCategoryInfo {"CategoryNo": N}`.
- **Multiple categories** — duplicate the template for each category and set a different `category_no` default, or pass `webhook_extra={"category_no": N}` per-job.
- **Large files** — base64 encoding adds ~33% overhead. For documents over ~20 MB consider `webhook_include_file=false` and fetching the file from the `download_url` in the standard payload.
- **Keyword fields** — if your category has mandatory keyword (dropdown) fields requiring `PreprocessIndexData` or `GetKeywordsByFieldNo`, use an adapter service instead of the direct template approach. See the guide for an example adapter implementation.

---

## Folder Polling

Drop files into the input directory and they are automatically picked up and processed:

```bash
# Via docker cp (named volume)
docker cp document.pdf redactor-backend:/data/input/

# Via host-mounted volume
cp document.pdf /your/host/path/input/
```

- Files are detected by scanning the directory at the configured poll interval
- Each file's SHA-256 hash is recorded so the same file is never processed twice
- Polled files use the system default redaction level and output mode

---

## Production Considerations

**Bind-mount a host volume** so data persists independently of Docker:
```yaml
# docker-compose.yml
volumes:
  redactor-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /srv/redactor/data
```

**Add authentication** — the API and UI have no authentication by default. Put nginx, Caddy, or Traefik in front with basic auth or OAuth before exposing to a network.

**Scale concurrency** — increase `WORKER_CONCURRENCY` in `.env` for higher throughput. CPU is the bottleneck for OCR-heavy workloads.

**Image size** — the backend image is ~3 GB due to spaCy's `en_core_web_lg` model. Build once and push to a private registry.

**Logs:**
```bash
docker compose logs -f backend
docker compose logs -f frontend
```

**Update:**
```bash
git pull
docker compose build
docker compose up -d
```

---

## Technology Stack

| Component | Technology |
|---|---|
| API framework | FastAPI |
| PII detection | Microsoft Presidio + spaCy `en_core_web_lg` |
| PDF processing | PyMuPDF (fitz) |
| OCR | Tesseract via pytesseract |
| Image processing | Pillow |
| Template rendering | Jinja2 |
| Database | SQLite via SQLAlchemy (async) |
| HTTP client | httpx (async webhook delivery) |
| Frontend | Vanilla HTML/CSS/JS |
| Web server | nginx |
| Container | Docker + docker compose |

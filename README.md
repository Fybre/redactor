# Redactor

A self-hosted document redaction service that automatically detects and blacks out personally identifiable information (PII) from PDFs and images. Deployed as a Docker stack with a web-based configuration dashboard.

---

## Features

- **Multi-format input** — PDF (text-layer and scanned/image-based), PNG, JPG, TIFF, BMP
- **Dual ingestion modes** — REST API upload or automatic folder polling
- **Dual output modes** — save to output directory or deliver via signed webhook
- **Four redaction levels** — Minimal → Standard → Aggressive → Maximum, plus fully custom entity selection
- **Custom profiles** — save named entity sets for reuse across jobs
- **Forensically sound PDF redaction** — removes text from the PDF content stream (not just visually covered); extracted text cannot be recovered
- **OCR redaction** — detects and redacts PII in scanned documents and images using Tesseract
- **Web dashboard** — job monitoring, configuration, profile management, webhook management
- **Docker deployment** — single `docker compose up` with persistent volumes

---

## Quick Start

```bash
git clone <repo>
cd redactor
cp .env.example .env
docker compose up -d
```

The web UI is available at **http://localhost:8080** (or the port set in `.env`).

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Docker Network                    │
│                                                     │
│  ┌──────────────────┐      ┌──────────────────────┐ │
│  │   frontend       │      │   backend            │ │
│  │   nginx:80       │─────▶│   FastAPI:8000        │ │
│  │                  │      │                      │ │
│  │  Static HTML/JS  │      │  • REST API          │ │
│  │  Reverse proxy   │      │  • Job worker        │ │
│  └──────────────────┘      │  • Folder poller     │ │
│           │                └──────────┬───────────┘ │
└───────────┼───────────────────────────┼─────────────┘
            │                           │
        Browser                  /data volume
                                  ├── input/
                                  ├── output/
                                  ├── originals/
                                  ├── temp/
                                  ├── redactor.db
                                  └── runtime_config.json
```

**Backend** — single Python process running FastAPI + two background asyncio tasks:
- **Job worker** — polls the database for queued jobs every second, processes them with configurable concurrency
- **Folder poller** — scans `/data/input` at a configurable interval, auto-submits new files as jobs

**Frontend** — static HTML/CSS/JS served by nginx, which also reverse-proxies all `/api/` requests to the backend.

**Storage** — SQLite database for job tracking; files stored on a named Docker volume.

---

## Redaction Pipeline

### Text-layer PDFs

For PDFs that contain a real text layer (digitally created, not scanned):

1. Extract words with bounding boxes using PyMuPDF
2. Reconstruct page text, building a character-offset → bounding-box map
3. Run Presidio NLP analysis on full page text
4. For each PII match, locate the corresponding word bounding boxes
5. Apply `add_redact_annot()` + `apply_redactions()` — this **removes the text from the PDF content stream**, not just paints over it
6. Save with garbage collection to remove orphaned objects

> Text removed this way cannot be recovered by selecting, copying, or forensic PDF analysis tools.

### Scanned PDFs and Images

For image-based PDFs and standalone image files:

1. Render each page to a high-resolution (300 DPI) PIL image
2. Run Tesseract OCR to extract words with pixel bounding boxes
3. Reconstruct text with character-offset → pixel-bbox map
4. Run Presidio on the OCR text
5. Draw filled black rectangles over PII bounding boxes using PIL
6. For PDFs: replace the page content with the redacted image

### Mixed PDFs

Each page is assessed individually — pages with a real text layer use pipeline 1; pages without (text density below threshold) use pipeline 2. Both pipelines are applied within a single output PDF.

---

## Redaction Levels

| Level | Entities Included |
|---|---|
| **Minimal** | Credit cards, SSN, IBAN, bank accounts, passports, NHS, Aadhaar, PAN, TFN, Medicare |
| **Standard** | + Person names, email, phone, driver's licence, IP address, medical licence, ITIN |
| **Aggressive** | + Locations/addresses, dates & times, URLs, nationality/religion/political groups, regional IDs |
| **Maximum** | + Organisations, ages, monetary values, facility names |
| **Custom** | Choose any combination of the above entity types, or save as a named profile |

---

## Configuration

All settings are configurable via the web UI (Configuration page) or by editing `.env` before first start.

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

Runtime settings (poll interval, concurrency, etc.) can also be changed live via the web UI without restarting the container.

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

### Submit a document

```bash
curl -X POST http://localhost:8080/api/v1/jobs/upload \
  -F file=@document.pdf \
  -F level=standard \
  -F output_mode=directory
```

**Form fields:**

| Field | Required | Values | Description |
|---|---|---|---|
| `file` | Yes | — | The file to redact |
| `level` | No | `minimal` `standard` `aggressive` `maximum` `custom` | Redaction level (default: `standard`) |
| `custom_entities` | If `level=custom` | JSON array | e.g. `["PERSON","EMAIL_ADDRESS"]` |
| `profile_name` | No | string | Use a saved profile (sets `level=custom`) |
| `output_mode` | No | `directory` `webhook` | Where to deliver the result |
| `webhook_url` | If `webhook` mode | URL | Endpoint to POST the completion event to |

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

**Response:**
```json
{
  "id": "a1b2c3d4-...",
  "filename": "document.pdf",
  "status": "completed",
  "level": "standard",
  "page_count": 3,
  "entities_found": {
    "PERSON": 4,
    "EMAIL_ADDRESS": 2,
    "PHONE_NUMBER": 1
  },
  "processing_ms": 1842,
  "created_at": "2026-03-18T10:00:00Z",
  "completed_at": "2026-03-18T10:00:01Z"
}
```

**Status values:** `queued` → `processing` → `completed` or `failed`

### Download redacted file

```bash
curl -O -J http://localhost:8080/api/v1/jobs/{job_id}/download
```

### List all jobs

```bash
curl "http://localhost:8080/api/v1/jobs?status=completed&page=1&per_page=20"
```

### Delete a job

```bash
curl -X DELETE http://localhost:8080/api/v1/jobs/{job_id}
```

### Retry a failed job

```bash
curl -X POST http://localhost:8080/api/v1/jobs/{job_id}/retry
```

### Get redaction report

```bash
curl http://localhost:8080/api/v1/jobs/{job_id}/report
```

### System stats

```bash
curl http://localhost:8080/api/v1/stats
```

### Get/update configuration

```bash
# Get current config
curl http://localhost:8080/api/v1/config

# Update config
curl -X PUT http://localhost:8080/api/v1/config \
  -H "Content-Type: application/json" \
  -d '{"default_redaction_level": "aggressive", "poll_interval_seconds": 30, ...}'
```

---

## Folder Polling

Drop files into the input directory and they will be automatically picked up and processed:

```bash
# Copy a file into the watched directory (from host, using docker cp)
docker cp document.pdf redactor-backend:/data/input/

# Or if using a host-mounted volume:
cp document.pdf /your/host/path/input/
```

- Files are detected by scanning the directory at the configured poll interval
- Each file's SHA-256 hash is recorded so the same file is never processed twice
- Processed files are moved out of `/data/input` to prevent re-processing
- Polled files use the system default redaction level and output mode

---

## Webhooks

When output mode is `webhook`, Redactor POSTs a JSON payload to your endpoint when a job completes:

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

### Verifying webhook signatures

If you configure a signing secret, each request includes:

```
X-Redactor-Signature: sha256=<hmac-hex>
X-Redactor-Timestamp: <unix-timestamp>
```

Verify in Python:
```python
import hmac, hashlib

def verify(body: bytes, secret: str, signature_header: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)
```

Redactor retries failed webhook deliveries 3 times with exponential backoff before marking the delivery as failed.

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

## Production Considerations

**Bind-mount a host volume** so data persists outside of Docker:
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

**Add authentication** — the API and UI have no authentication by default. Put nginx or a reverse proxy (Caddy, Traefik) in front with basic auth or OAuth before exposing to a network.

**Scale concurrency** — increase `WORKER_CONCURRENCY` in `.env` for higher throughput. CPU is the bottleneck for OCR-heavy workloads; allocate more cores to the container accordingly.

**Image size** — the backend image is ~3 GB due to spaCy's `en_core_web_lg` model. Build once and push to a private registry.

**Logs:**
```bash
docker compose logs -f backend
docker compose logs -f frontend
```

**Update:**
```bash
docker compose pull   # if using registry
docker compose build  # if building locally
docker compose up -d
```

---

## Supported Entity Types

Run `GET /api/v1/config/entities` or visit Configuration → Redaction Profiles to see the full list of supported entity types with descriptions.

Key types include: `PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `LOCATION`, `DATE_TIME`, `CREDIT_CARD`, `US_SSN`, `IBAN_CODE`, `IP_ADDRESS`, `URL`, `ORGANIZATION`, `NRP`, `MEDICAL_LICENSE`, `US_PASSPORT`, `US_DRIVER_LICENSE`, and many regional identifiers (AU, IN, SG, UK).

---

## Therefore™ Integration

This section covers the full round-trip integration with the Therefore document management
system. There are two approaches — choose whichever fits your setup.

---

### Approach A — Webhook Template (no adapter required)

Redactor posts **directly to Therefore's `CreateDocument` endpoint** using a saved Jinja2
template to shape the payload. No intermediate service needed.

```
┌────────────┐  multipart POST   ┌──────────┐  CreateDocument (template)  ┌────────────┐
│ Therefore  │ ────────────────▶ │ Redactor │ ──────────────────────────▶ │ Therefore  │
│ (outgoing) │                   │          │                              │ (incoming) │
└────────────┘                   └──────────┘                              └────────────┘
```

#### Step 1A — Create the webhook template

In **Configuration → Webhook Templates**, create a template named `therefore_create_document`
(a sample is pre-loaded). The body is Jinja2 JSON that maps Redactor's job variables directly
to Therefore's `CreateDocument` structure:

```jinja
{
  "CategoryNo": {{ category_no | default(57) }},
  "CheckInComments": "Redacted by Redactor — {{ filename }}",
  "IndexDataItems": [
    {
      "StringIndexData": {
        "FieldName": "Document_Name",
        "FieldNo": 0,
        "DataValue": "{{ stem }}"
      }
    },
    {
      "StringIndexData": {
        "FieldName": "Redaction_Level",
        "FieldNo": 0,
        "DataValue": "{{ level }}"
      }
    },
    {
      "StringIndexData": {
        "FieldName": "Job_ID",
        "FieldNo": 0,
        "DataValue": "{{ job_id }}"
      }
    },
    {
      "DateIndexData": {
        "FieldName": "Redaction_Date",
        "FieldNo": 0,
        "DataISO8601Value": "{{ completed_at }}"
      }
    },
    {
      "IntIndexData": {
        "FieldName": "Page_Count",
        "FieldNo": 0,
        "DataValue": {{ page_count }}
      }
    }
  ],
  "Streams": [
    {% if file_data %}
    {
      "FileName": "{{ file_name }}",
      "FileDataBase64JSON": "{{ file_data }}",
      "NewStreamInsertMode": 0
    }
    {% endif %}
  ],
  "DoFillDependentFields": true
}
```

**Available template variables:** `job_id`, `filename`, `stem` (filename without extension),
`status`, `level`, `page_count`, `entities_found`, `total_entities`, `processing_ms`,
`completed_at`, `file_data` (base64), `file_name`, `file_size_bytes`.

#### Step 2A — Therefore sends the document to Redactor

Configure Therefore's outgoing REST call to POST directly to Redactor, specifying the template
name and Therefore's own endpoint as the webhook URL:

```
POST http://redactor-host:8080/api/v1/jobs/upload
Content-Type: multipart/form-data; boundary=----ThereforeBoundary

------ThereforeBoundary
Content-Disposition: form-data; name="file"; filename="invoice.pdf"
Content-Type: application/pdf

<binary file content>
------ThereforeBoundary
Content-Disposition: form-data; name="level"

standard
------ThereforeBoundary
Content-Disposition: form-data; name="output_mode"

webhook
------ThereforeBoundary
Content-Disposition: form-data; name="webhook_url"

https://acme.thereforeonline.com/theservice/v0001/restun/CreateDocument
------ThereforeBoundary
Content-Disposition: form-data; name="webhook_template"

therefore_create_document
------ThereforeBoundary
Content-Disposition: form-data; name="webhook_include_file"

true
------ThereforeBoundary
Content-Disposition: form-data; name="webhook_headers"

{"Authorization":"Basic c3ZjX3JlZGFjdG9yOnBhc3N3b3Jk","TenantName":"acme","Content-Type":"application/json"}
------ThereforeBoundary--
```

> `Authorization` is Basic auth (`base64(username:password)`). `TenantName` is only required
> for Therefore Online (`*.thereforeonline.com`) — set it to your subdomain prefix.

**Equivalent curl (for testing):**

```bash
curl -X POST http://redactor-host:8080/api/v1/jobs/upload \
  -F file=@invoice.pdf \
  -F level=standard \
  -F output_mode=webhook \
  -F webhook_url=https://acme.thereforeonline.com/theservice/v0001/restun/CreateDocument \
  -F webhook_template=therefore_create_document \
  -F webhook_include_file=true \
  -F 'webhook_headers={"Authorization":"Basic c3ZjX3JlZGFjdG9yOnBhc3N3b3Jk","TenantName":"acme","Content-Type":"application/json"}'
```

Redactor queues the job, renders the template on completion, and POSTs the result directly to
Therefore's `CreateDocument` — no adapter needed.

---

### Approach B — Adapter Service

Use this approach when you need `PreprocessIndexData` (auto-numbering, calculated fields,
mandatory keyword lookups) before calling `CreateDocument`, or when you need more control over
the index data than the template can provide.

```
┌────────────┐  multipart POST   ┌──────────┐  webhook (standard JSON)  ┌─────────┐  CreateDocument  ┌────────────┐
│ Therefore  │ ────────────────▶ │ Redactor │ ────────────────────────▶ │ Adapter │ ───────────────▶ │ Therefore  │
│ (outgoing) │                   │          │                            │         │                  │ (incoming) │
└────────────┘                   └──────────┘                            └─────────┘                  └────────────┘
```

#### Step 1B — Therefore sends the document to Redactor

```
POST http://redactor-host:8080/api/v1/jobs/upload
Content-Type: multipart/form-data; boundary=----ThereforeBoundary

------ThereforeBoundary
Content-Disposition: form-data; name="file"; filename="invoice.pdf"
Content-Type: application/pdf

<binary file content>
------ThereforeBoundary
Content-Disposition: form-data; name="level"

standard
------ThereforeBoundary
Content-Disposition: form-data; name="output_mode"

webhook
------ThereforeBoundary
Content-Disposition: form-data; name="webhook_url"

http://adapter-host:8090/redactor-callback
------ThereforeBoundary
Content-Disposition: form-data; name="webhook_include_file"

true
------ThereforeBoundary
Content-Disposition: form-data; name="webhook_secret"

your-shared-secret
------ThereforeBoundary--
```

**Equivalent curl:**

```bash
curl -X POST http://redactor-host:8080/api/v1/jobs/upload \
  -F file=@invoice.pdf \
  -F level=standard \
  -F output_mode=webhook \
  -F webhook_url=http://adapter-host:8090/redactor-callback \
  -F webhook_include_file=true \
  -F webhook_secret=your-shared-secret
```

#### Step 2B — Redactor calls the adapter with the standard payload

When the job completes, Redactor POSTs its standard JSON payload (signed with HMAC) to the
adapter:

```json
{
  "event": "job.completed",
  "job_id": "a1b2c3d4-...",
  "filename": "invoice.pdf",
  "status": "completed",
  "level": "standard",
  "page_count": 3,
  "entities_found": { "PERSON": 4, "EMAIL_ADDRESS": 2 },
  "processing_ms": 2341,
  "completed_at": "2026-03-18T12:00:01Z",
  "file_name": "invoice_redacted_a1b2c3d4.pdf",
  "file_size_bytes": 94208,
  "file_data": "JVBERi0xLjQKJeLjz9MKN..."
}
```

#### Step 3B — The adapter saves the document back to Therefore

**`adapter.py`:**

```python
import hashlib, hmac, os, requests
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

REDACTOR_WEBHOOK_SECRET = os.environ["REDACTOR_WEBHOOK_SECRET"]
THEREFORE_BASE_URL      = os.environ["THEREFORE_BASE_URL"]
THEREFORE_USERNAME      = os.environ["THEREFORE_USERNAME"]
THEREFORE_PASSWORD      = os.environ["THEREFORE_PASSWORD"]
THEREFORE_TENANT        = os.environ.get("THEREFORE_TENANT", "")
THEREFORE_CATEGORY_NO   = int(os.environ["THEREFORE_CATEGORY_NO"])


def therefore_session():
    s = requests.Session()
    s.auth = (THEREFORE_USERNAME, THEREFORE_PASSWORD)
    s.headers.update({"Content-Type": "application/json; charset=utf-8"})
    if THEREFORE_TENANT:
        s.headers["TenantName"] = THEREFORE_TENANT
    return s


def therefore_post(session, endpoint, payload):
    resp = session.post(f"{THEREFORE_BASE_URL}/{endpoint}", json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def verify_signature(body: bytes, header: str) -> bool:
    expected = hmac.new(
        REDACTOR_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@app.post("/redactor-callback")
async def redactor_callback(request: Request):
    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Redactor-Signature", "")):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    if payload.get("status") != "completed" or not payload.get("file_data"):
        return {"ok": True, "skipped": True}

    session = therefore_session()

    # Build index data — adjust FieldNo values to match your category definition.
    # Use POST /restun/GetCategoryInfo {"CategoryNo": N} to list field numbers.
    index_items = [
        {"StringIndexData": {"FieldNo": 101, "DataValue": payload["filename"]}},
        {"StringIndexData": {"FieldNo": 102, "DataValue": payload["job_id"]}},
    ]

    # PreprocessIndexData — fills defaults, auto-numbering, calculated fields
    preprocessed = therefore_post(session, "PreprocessIndexData", {
        "CategoryNo": THEREFORE_CATEGORY_NO,
        "FillDependentFields": True,
        "ResetToDefaults": True,
        "DoCalculateFields": True,
        "GetAutoAppendIxData": False,
        "ExcludeReduntantForFillDependentFields": True,
        "IndexData": {"IndexDataItems": index_items},
    })

    # CreateDocument — file_data is already base64, pass it through directly
    result = therefore_post(session, "CreateDocument", {
        "TheDocument": {
            "CategoryNo": THEREFORE_CATEGORY_NO,
            "IndexDataItems": preprocessed["IndexData"]["IndexDataItems"],
            "Streams": [{
                "StreamNo": 0,
                "FileName": payload["file_name"],
                "FileData": payload["file_data"],
            }],
        }
    })
    return {"ok": True, "doc_no": result.get("DocNo")}
```

**Run the adapter:**

```bash
pip install fastapi uvicorn requests

REDACTOR_WEBHOOK_SECRET=your-shared-secret \
THEREFORE_BASE_URL=https://acme.thereforeonline.com/theservice/v0001/restun \
THEREFORE_USERNAME=svc_redactor \
THEREFORE_PASSWORD=password \
THEREFORE_CATEGORY_NO=8 \
uvicorn adapter:app --host 0.0.0.0 --port 8090
```

Or as a Docker service alongside Redactor — add it to `docker-compose.yml`:

```yaml
  adapter:
    build:
      context: ./adapter
    container_name: redactor-therefore-adapter
    restart: unless-stopped
    environment:
      - REDACTOR_WEBHOOK_SECRET=your-shared-secret
      - THEREFORE_BASE_URL=https://acme.thereforeonline.com/theservice/v0001/restun
      - THEREFORE_USERNAME=svc_redactor
      - THEREFORE_PASSWORD=password
      - THEREFORE_CATEGORY_NO=8
    ports:
      - "8090:8090"
    networks:
      - internal
```

---

### Therefore integration notes

- **Approach A vs B** — use Approach A (template) when your Therefore category's fields are
  straightforward and index data can be derived from the job. Use Approach B (adapter) when
  you need `PreprocessIndexData`, calculated fields, or dynamic keyword lookups.
- **Field numbers** — in the template or adapter, set `FieldNo` to match your Therefore
  category definition. Look them up with:
  `POST /restun/GetCategoryInfo {"CategoryNo": 8, "IsAccessMaskNeeded": false}`.
- **Keyword fields** — if the category has mandatory keyword (dropdown) fields, resolve their
  display values to `KeywordNo` integers using `GetKeywordsByFieldNo` before calling
  `CreateDocument`. This usually requires the adapter approach.
- **Tenant** — for Therefore Online (`*.thereforeonline.com`), include `"TenantName":"acme"`
  in `webhook_headers` (Approach A) or set `THEREFORE_TENANT` in the adapter env (Approach B).
- **Testing** — use the Redactor upload page to submit a test document with
  `output_mode=webhook` and inspect the job detail page to confirm `webhook_sent=true`.
- **Large files** — base64 encoding inflates file size by ~33%. For documents over ~20 MB,
  consider setting `webhook_include_file=false` and fetching the file separately from
  `/api/v1/jobs/{job_id}/download` using the `download_url` in the standard payload.

---

## Technology Stack

| Component | Technology |
|---|---|
| API framework | FastAPI 0.110 |
| PII detection | Microsoft Presidio + spaCy `en_core_web_lg` |
| PDF processing | PyMuPDF (fitz) |
| OCR | Tesseract via pytesseract |
| Image processing | Pillow |
| Database | SQLite via SQLAlchemy (async) |
| HTTP client | httpx (webhook delivery) |
| Frontend | Vanilla HTML/CSS/JS |
| Web server | nginx |
| Container | Docker + docker compose |

# ImageExtractor

Paste a public web page URL, extract every image on **that single page**, preview them in a gallery and download them one by one or all together as a ZIP.

- **Backend:** FastAPI · HTTPX · BeautifulSoup (lxml) · Selenium (headless Chrome) · Pillow
- **Frontend:** Next.js 16 (App Router) · React 19 · TypeScript · Tailwind CSS 4

The scanner fetches pages with HTTPX first. It launches headless Chrome only when a page needs JavaScript: an SPA shell, few images, placeholder-only `<img>` tags, or a static request that was refused. No crawling: only the URL you enter is scanned.

---

## Quick start

### Prerequisites
- Python 3.11+ (developed on 3.14)
- Node.js 20+ (developed on 24)
- Google Chrome. Selenium Manager downloads a matching chromedriver automatically on first use.

### 1. Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional – every setting has a default
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Interactive API docs: http://localhost:8000/docs

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local      # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev                     # or: npm run build && npm start
```

Open http://localhost:3000.

> `NEXT_PUBLIC_API_BASE_URL` is inlined at **build** time. Rebuild the frontend after changing it.
> If the frontend runs on a different origin, add that origin to `CORS_ORIGINS` in `backend/.env`.

### Tests

```bash
cd backend
pytest            # 70 tests: SSRF validation, extraction, downloader, ZIP streaming, API flow
```

The tests use `httpx.MockTransport` and a fake browser, so they need neither network access nor Chrome.

---

## How it works

```
Next.js UI ──POST /api/scrape──▶ FastAPI ──▶ JobManager (asyncio background task)
     ▲  poll GET /api/scrape/{id}                │
     │                                           ├─ 1. validate URL (SSRF)
     │                                           ├─ 2. HTTPX fetch (manual, re-validated redirects)
     │                                           ├─ 3. BeautifulSoup extraction
     │                                           ├─ 4. headless Chrome fallback (only if needed)
     │                                           ├─ 5. linked stylesheets → CSS background images
     │                                           └─ 6. concurrent download + validation → per-job disk cache
     └── previews / single downloads / streamed ZIP served from that cache
```

Each image is downloaded **once**, during the scan. After validation (magic bytes plus Pillow decoding), its dimensions and real type are recorded and it is cached on disk. Previews, single downloads and the ZIP all come from that cache. Expired jobs, and their files, are removed by a background sweeper after `JOB_TTL_SECONDS`. All job files are also removed on shutdown.

### Image sources detected
- `<img>` `src`, `srcset` (the largest candidate only), and lazy-load attributes: `data-src`, `data-srcset`, `data-lazy-src`, `data-original`, `data-lazy` and more.
- `<picture>`/`<source>`, collapsed to one image per picture.
- CSS: inline `style`, `<style>` blocks, linked stylesheets (`background`, `image-set()`, `list-style-image` and others; fonts are ignored), and `data-bg`-style attributes.
- Metadata: `og:image`, `twitter:image`, `itemprop=image`, JSON-LD, icons, and `<link rel=preload as=image>`.
- Video posters, SVG `<image>`, `<input type=image>`, and links that point directly to image files.
- JavaScript-loaded images (browser mode): the rendered DOM, computed `background-image` styles (including `::before`/`::after`), and the Resource Timing API.

Supported formats: JPEG, PNG, GIF, WebP, AVIF, SVG, BMP and ICO. The **content** determines the type. Extensions and `Content-Type` headers are not trusted.

---

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/scrape` | `{"url": "...", "render_mode": "auto" \| "static" \| "browser"}` → `202 {job_id, status}`; `400` for invalid or blocked URLs |
| `GET` | `/api/scrape/{job_id}` | Status, progress (`stage`, `message`, `processed/total`, `percent`), warnings, error and images |
| `GET` | `/api/image/{image_id}` | Inline image preview |
| `GET` | `/api/download/{job_id}/{image_id}` | Single image as an attachment |
| `GET` | `/api/download/{job_id}` | Streamed ZIP of all images (`409` if the job is not finished) |
| `GET` | `/api/health` | Liveness check |

Job statuses: `queued → fetching → rendering → extracting → downloading → completed | failed`.

---

## Project structure

```
backend/
  app/
    main.py                  app factory, lifespan (HTTP client, browser pool, job manager), CORS
    config.py                all settings from environment variables / .env
    routes/scrape.py         POST/GET scrape job endpoints
    routes/download.py       preview, single download, streamed ZIP
    services/http_scraper.py SSRF-safe HTTPX fetching: manual redirects, retries, size caps
    services/selenium_scraper.py  headless Chrome pool: explicit waits, controlled scrolling
    services/image_extractor.py   pure HTML/CSS → normalized, de-duplicated image URLs
    services/image_downloader.py  concurrent download, validation, content de-dup, disk cache
    services/zip_service.py  incremental (streaming) ZIP writer
    services/job_manager.py  JobStore protocol, in-memory store, pipeline, TTL cleanup
    utils/validator.py       URL + IP validation (SSRF protection)
    utils/helpers.py         ids, safe filenames, srcset/CSS parsing, type sniffing, retry
    schemas/scrape_schema.py Pydantic request/response models
  tests/
frontend/
  app/                       layout, page, global styles
  components/                UrlForm, JobProgress, Gallery, ImageCard, Lightbox, ToastProvider, ...
  lib/                       typed API client, polling hook, formatting helpers
```

To persist jobs or run several workers, implement the `JobStore` protocol in `services/job_manager.py` (for example on Redis) and swap it in `main.py`. Routes and services don't change.

---

## Security

- **SSRF protection.** Only `http`/`https` URLs on allowed ports are accepted, with no credentials in the URL. Hostnames are resolved, and the request is refused if **any** resolved address is loopback, private, link-local (including cloud metadata at `169.254.169.254`), CGNAT, multicast, reserved or unspecified. IPv4-mapped, 6to4, Teredo and NAT64 IPv6 addresses are unwrapped and checked too, as are obfuscated IP literals (`2130706433`, `0x7f.1`, `127.1`) and names such as `localhost`, `*.local` and `*.internal`.
- **Re-validation.** Every redirect hop, stylesheet and image URL is validated again before it is requested. Inside Chrome, CDP `Network.setBlockedURLs` blocks private-range sub-resources, and the final page URL is re-validated.
- **Download limits.** Per-image and per-job byte caps are enforced while streaming. There is also a cap on images per job and a decompression-bomb pixel limit in Pillow.
- **Content validation.** Images are checked by magic bytes and Pillow decoding, so HTML error pages served as `.jpg` are rejected.
- **No path traversal.** Ids are server-generated hex and validated by regex, and files are looked up through the job store only. Filenames are sanitized, use the detected extension, and are de-duplicated case-insensitively.
- **Hardened image responses.** Served images carry `X-Content-Type-Options: nosniff` and a sandboxing CSP, so scripted SVGs cannot run on the API origin.
- **No access-control bypass.** Logins, paywalls, CAPTCHAs and bot checks are not bypassed. `401`/`403` responses and detected challenge pages are reported as "access denied". The default User-Agent is honest and identifies the tool.

**Known limitation:** a small DNS-rebinding window remains between validation and connection. Run the backend in an egress-restricted network for defense in depth.

---

## Configuration

All settings are environment variables; see [`backend/.env.example`](backend/.env.example). The most relevant ones:

| Variable | Default | Purpose |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:3000,...` | Allowed frontend origins |
| `USER_AGENT` | `ImageExtractor/1.0 (+https://example.com/...)` | **Replace with your own contact URL or email** |
| `MAX_BROWSER_SESSIONS` | `2` | Concurrent headless Chrome instances |
| `MAX_CONCURRENT_DOWNLOADS` | `8` | Parallel image downloads per job |
| `MAX_CONCURRENT_JOBS` | `4` | Jobs processed at once (others wait in `queued`) |
| `MAX_IMAGES_PER_JOB` | `300` | Images kept per scan |
| `MAX_IMAGE_BYTES` / `MAX_JOB_TOTAL_BYTES` | 15 MB / 300 MB | Size limits |
| `SELENIUM_MAX_SCROLLS` / `SELENIUM_SCROLL_BUDGET` | `15` / `15s` | Lazy-load scrolling bounds |
| `JOB_TTL_SECONDS` | `3600` | How long results and cached files are kept |

---

## Notes and limitations

- Use **Browser** mode for JavaScript-heavy pages when Auto mode doesn't detect them. Use **Static** mode for the fastest scan.
- Images drawn on `<canvas>` or served as `blob:`/`data:` URLs are not extracted.
- Infinite-scroll pages are scrolled only up to the configured budget.
- The in-memory job store is process-local, so run a single Uvicorn worker unless you plug in a shared store.
- Some antivirus or web-filtering software intercepts script-initiated archive downloads. When that happens, the UI hands the ZIP download to the browser's own download manager.
- Respect website terms of service and copyright when downloading images.

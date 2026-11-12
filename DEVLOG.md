# DEVLOG — PakWheels Scraper & Visualizer

> Single source of truth for roadmap progress, state changes and hand-off notes.
> **Last updated:** 2026-10-26
> **Current status:** `Feature 2: Complete`

---

## 1. Status Board

| Track | Scope | Status |
|---|---|---|
| Baseline (Sessions 1–2) | Polite scraper, Flask UI, Plotly scatter, CSV/JSON/HTML exports, manual listing edits | **Done** |
| Feature 1 | FMV & Deal Rating Engine — curve fitting, residuals, z-scores, deal surfacing | **1.1 & 1.2 Done; 1.3 deferred** |
| **Feature 2** | Multi-Model Comparative Visualizations — multi-trace overlay, city-wise boxplots | **Done** |
| Feature 3 | Historical Tracking & Price Drops — SQLite storage, snapshot comparisons, price-drop badging | Not started |
| Feature 4 | Advanced Filters & PDF Market Report — UI sliders, PDF generation | Not started |

### Active Micro-Task

**`Task 4.1: Comprehensive PakWheels Search Filter Integration — Complete`**

Delivered in Task 4.1:
- **Search URL Builder (`pw_scraper.py`):** Added `build_search_path` and updated `search_url` to support price range (`pr_min`, `pr_max`), year range (`yr_min`, `yr_max`), mileage (`km_min`, `km_max`), engine capacity (`cc_min`, `cc_max`), cities (`ct_<city>`), transmissions (`transmission_auto`, `transmission_manual`), fuel types (`fuel_petrol`, `fuel_diesel`, `fuel_hybrid`, `fuel_electric`), body types (`bt_<slug>`), seller types (`se_individual`, `se_dealer`), and color (`cl_<slug>`).
- **Generic Search & Scraper Backend (`pw_scraper.py`, `web_app.py`):** Enhanced card parser to dynamically deduce make and model from ad URLs/titles when scraping generic filter searches; expanded `estimate_scrape`, `scrape_pakwheels_model`, and API endpoints (`/api/estimate`, `/api/scrape`) to accept custom search filters without strict make/model requirement.
- **Web UI Filter Matrix (`templates/index.html`, `static/style.css`, `static/app.js`):** Added collapsible "Custom Search & Arbitrage Filters" matrix with inputs, city/body dropdowns, checkboxes, and clear actions, integrated into `buildBody()`.
- **Test Coverage (`tests/test_parsers.py`):** Added unit tests for URL filter building, composite multi-filter paths, and dynamic card parsing (82/82 tests passing).

---

### Previous Track Status

**`Feature 2: Complete`**

Delivered in Micro-Task 2.1 & 2.2:

- **Target Picker & Comparison Builder (`templates/index.html`, `static/style.css`, `static/app.js`):**
  - Added interactive multi-target picker in Section 1 with `+ Compare another car` button, comparison tag tray (up to 4 models, per `MAX_TARGETS = 4`), and individual badge removal (`×`) or "Clear all".
  - Frontend `buildBody()` transparently formats `targets: [{make, model}, ...]` or single `make`/`model` payloads for estimation and scraping.
- **Multi-Trace Scatter & Per-Model Trendlines (`static/app.js`):**
  - Grouped Plotly scatter traces per model with distinct colors from standard palette.
  - Per-model OLS regression trendlines computed client-side with slope and R² labels for comparative price trajectory visualization.
  - Interactive legend filtering (`itemclick: "toggle"`, `itemdoubleclick: "toggleothers"`).
- **Regional Price Distribution (City-Wise Boxplots) (`templates/index.html`, `static/style.css`, `static/app.js`):**
  - Added view tabs to switch between **Scatter & Trends** and **Regional Price Distribution (City-Wise Boxplots)**.
  - Boxplot engine renders regional price distributions across key markets (Islamabad, Lahore, Karachi, Rawalpindi), with grouped boxmode when comparing multiple models.
  - Dedicated toolbar controls for price units (lacs PKR vs raw PKR), jittered point/outlier toggles, and featured ads filtering.
- **Unit & Integration Tests (`tests/test_parsers.py`, `tests/test_edits.py`):**
  - 79 offline tests passing covering multi-model normalization, comparison metadata, export schemas, Flask request handling, and job creation.

---

## 2. Project Structure (actual, as of this entry)

```text
PAKWHEELS_DATA/
├─ pw_scraper.py            # Core library: robots guard, polite session, parsers, catalogue,
│                           # ETA, multi-target scrape loop, payload, exports, edits, FMV
├─ web_app.py               # Flask app, Job/JobManager, background scrape worker, REST API
├─ PW_SCRIPT.PY             # Thin CLI: --estimate, --catalogue, --self-test, --scatter
├─ config.json              # Catalogue cache + politeness defaults
├─ requirements.txt         # requests, bs4/soupsieve, flask, pandas, numpy, scipy
├─ README.md                # Install/run/API/compliance documentation
├─ DEVLOG.md                # This file
├─ SESSION-1-REPORT.md      # Baseline build + live verification notes
├─ SESSION-2-REPORT.md      # Manual-edit endpoint + test hardening notes
├─ templates/
│  └─ index.html            # Single-page UI: target builder, ETA, progress, scatter/box tabs, editor
├─ static/
│  ├─ app.js                # Fetch/poll, target tray, scatter + boxplots, preview table, edit dialog
│  ├─ style.css             # UI styling: target tags, view tabs, toolbars, deal badges
│  └─ vendor/plotly.min.js  # Local Plotly bundle (offline-safe)
├─ tests/
│  ├─ test_parsers.py       # 50 offline parser/robots/ETA/export/FMV/multi-model payload tests
│  └─ test_edits.py         # 29 endpoint/edit/multi-target integration tests
├─ data/
│  └─ runs/<job_id>/        # listings.csv, listings.json, meta.json, scatter.html
└─ logs/
   └─ pakwheels.log         # Rotating application log
```

---

## 3. Baseline Capabilities (do not regress)

- **Compliance:** every URL passes `assert_url_allowed()`; process-wide 2.0 s rate floor; retry/backoff on 429/5xx; blocked `sortby`/`nf`/`direct`/private paths.
- **Scrape flow:** estimate (ads, pages, latency, ETA) → background job → live progress → dedupe by `listing_id` → payload/preview/files. Supports single and multi-model comparison targets up to 4 models.
- **Outputs:** CSV (UTF-8 BOM), JSON, standalone Plotly HTML, `meta.json`, plus `scatter.html` under `data/runs/<job_id>/`.
- **Manual corrections:** `POST /api/listings/<job_id>/edit`; 12 editable fields; `refresh_job_outputs()` rebuilds chart payload, preview and all on-disk exports so corrections never drift between views.
- **Tests:** 79 unit/integration tests passing (50 in `tests/test_parsers.py` + 29 in `tests/test_edits.py`).

---

## 4. Architecture Hooks for New Features

| Layer | File / symbol | Role for roadmap work |
|---|---|---|
| Analytics | `pw_scraper.py` → `linear_fit()`, `calculate_fmv_and_deal_scores()`, `build_scatter_payload()` | Existing stats/trend pattern; Feature 1 extends here |
| Job outputs | `web_app.py` → `refresh_job_outputs()` | **Single rebuild path** — keeps payload, preview, CSV/JSON/HTML aligned |
| API | `web_app.py` → `/api/result/<job_id>`, `/api/download/<job_id>/<fmt>`, `/api/scrape` | Payload and multi-model export extension points |
| Job state | `web_app.py` → `Job`, `JobManager` | Multi-target progress bookkeeping; ready for SQLite persistence in Feature 3 |
| Chart | `static/app.js` → `drawChart()`, `drawScatter()`, `drawBoxplots()` | Multi-model scatter overlay, per-model trendlines, regional boxplots |
| Target builder | `static/app.js` → `addCurrentTarget()`, `renderTargetTray()` | Multi-model target builder tray |
| Table | `static/app.js` → `renderPreview()`, `applyEditedPayload()` | Deal badges, FMV/residual columns, price-drop badges |
| Page | `templates/index.html` | Sections 1–4, target picker tray, view tabs (scatter vs boxplots) |

---

## 5. Roadmap Breakdown

### Feature 1 — FMV & Deal Rating Engine

| ID | Micro-task | Target files | Status |
|---|---|---|---|
| 1.1 | Analytics engine + polynomial year→price fit (R², RMSE) + per-listing FMV/residual/z-score/deal labels + tests | `pw_scraper.py`, `tests/test_parsers.py` | **Done** |
| 1.2 | Payload + job-output wiring + UI deal surfacing: FMV point fields/stats, deal colouring, hover details, preview badges/filter, stats cards | `pw_scraper.py`, `web_app.py`, `templates/index.html`, `static/app.js`, `static/style.css`, tests | **Done** |
| 1.3 | Exponential depreciation fit (log-linear OLS) + model selection (poly vs exp by RMSE/AIC) | `pw_scraper.py`, tests | Not started |
| 1.4 | UI: fitted curve overlay + confidence band | `static/app.js`, `templates/index.html`, `static/style.css` | Not started |

### Feature 2 — Multi-Model Comparative Visualizations

| ID | Micro-task | Target files | Status |
|---|---|---|---|
| 2.1 | Support Multi-Model Scrape & Payload Generation (targets parameter, make_model stamping, meta.json labeling, tests) | `pw_scraper.py`, `web_app.py`, `static/app.js`, `templates/index.html`, tests | **Done** |
| 2.2 | Multi-model overlay with individual trendlines & regional city-wise boxplots in UI | `static/app.js`, `templates/index.html`, `static/style.css`, tests | **Done** |

### Feature 3 — Historical Tracking & Price Drops

| ID | Micro-task | Target files | Status |
|---|---|---|---|
| 3.1 | SQLite schema (`data/market.db`): runs, listings, snapshots; idempotent migration | new `storage.py`, tests | Not started |
| 3.2 | Persist every completed run; snapshot comparison (new / removed / price changed) | `web_app.py`, `storage.py`, tests | Not started |
| 3.3 | Price history API + sparkline + price-drop badge in preview | `web_app.py`, `static/app.js`, UI | Not started |
| 3.4 | Re-scrape/refresh flow, retention policy, history export | `web_app.py`, `storage.py`, docs | Not started |

---

## 6. Session Log

### 2026-10-18 — Micro-Task 1.1 complete
- Core FMV calculation engine implemented with polynomial fitting and fallbacks.
- Verified test suite: 67 tests passing.

### 2026-10-18 — Micro-Task 1.2 complete
- FMV, residuals, and deal ratings exposed in payload, UI table badges, and Plotly color palette.
- Verified test suite: 70 tests passing.

### 2026-10-19 — Micro-Task 2.1 complete
- Scrape functions accept multi-target lists, stamping `make_model` per point and recording comparative metadata in `meta.json`.
- Verified test suite: 77 tests passing.

### 2026-10-26 — Micro-Task 2.2 complete (Feature 2 Finished)
- Multi-target tray with `+ Compare another car`, tags, and clear controls implemented in UI.
- Multi-trace scatter with individual per-model OLS trendlines and interactive legend isolation.
- Regional price distribution tab with grouped/overlay boxplots across Islamabad, Lahore, Karachi, and Rawalpindi.
- Multi-target payload validation tests added in `tests/test_edits.py`.
- Verification: **79 tests OK**, JavaScript syntax clean, layout verified.

---

## 7. Next Step

**Feature 3 - Micro-Task 3.1:** SQLite schema (`data/market.db`) for run snapshots, listings persistence, and idempotent table migrations.

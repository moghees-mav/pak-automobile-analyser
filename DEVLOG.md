# DEVLOG — PakWheels Scraper & Visualizer

> Single source of truth for roadmap progress, state changes and hand-off notes.
> **Last updated:** 2026-10-19
> **Current status:** `Feature 2 - Micro-Task 2.1: Complete`

---

## 1. Status Board

| Track | Scope | Status |
|---|---|---|
| Baseline (Sessions 1–2) | Polite scraper, Flask UI, Plotly scatter, CSV/JSON/HTML exports, manual listing edits | **Done** |
| Feature 1 | FMV & Deal Rating Engine — curve fitting, residuals, z-scores, deal surfacing | **1.1 & 1.2 Done; 1.3 deferred** |
| **Feature 2** | Multi-Model Comparative Visualizations — multi-trace overlay, city-wise boxplots | **In progress — 2.1 Complete; 2.2 next** |
| Feature 3 | Historical Tracking & Price Drops — SQLite storage, snapshot comparisons, price-drop badging | Not started |
| Feature 4 | Advanced Filters & PDF Market Report — UI sliders, PDF generation | Not started |

### Active Micro-Task

**`Feature 2 - Micro-Task 2.1: Complete`**

Delivered (2026-10-19):

- `pw_scraper.py` scraping methods updated: `scrape_pakwheels_model()`, `estimate_scrape()`,
  and unified entry point `scrape_pakwheels()` now accept a list of model targets
  (e.g., `[("honda", "civic"), ("toyota", "corolla")]`) as parameter `targets` or as
  the first argument, delegating smoothly to `scrape_pakwheels_targets()`.
- `build_scatter_payload()` now maintains distinct `make`, `model`, and `make_model`
  identifiers per listing point. In comparison runs, it sets `meta.comparison = True`,
  `meta.make_model` headline (`"Honda Civic vs Toyota Corolla"`), `meta.make_models`,
  `meta.make_model_counts`, `meta.targets`, and exposes `facets.make_model`.
- Dataset exports (`listings.csv`, `listings.json`) now reliably guarantee the presence
  and population of the `make_model` column for every row, and `meta.json` is correctly
  enriched with multi-model target comparisons via `save_run()` and `merge_run_meta()`.
- UI & Visualizer: added `<option value="make_model">Model</option>` to `<select id="color-by">`
  in `templates/index.html`. `static/app.js` automatically sets `color-by` to `"make_model"`
  when loading a comparison job. Standalone HTML export (`render_scatter_html`) defaults
  to grouping traces by `make_model` for comparison runs.
- CLI: `PW_SCRIPT.PY --scatter` updated to pass targets to `build_scatter_payload()`.
- Unit test coverage: added `MultiModelPayloadTests` (7 new tests in `tests/test_parsers.py`)
  verifying target normalization, comparison labels, multi-model payload generation,
  empty-frame handling, CSV/JSON/meta.json output parity, HTML render, and scraping target delegation.
- Verification: Full test suite passing (**77 tests OK**: 50 parser + 27 web/edit).

**Acceptance criteria:** implementation + deterministic offline tests pass, existing
suite stays green, DEVLOG updated. Fitted-curve overlay and confidence bands remain
in 1.4.

**Re-scope note (supersedes the earlier 1.2 plan):** deal surfacing was requested
as Micro-Task 1.2, so payload wiring and UI landed now. The planned log-linear
exponential fit and polynomial-vs-exponential model selection move to **1.3**;
the remaining curve/confidence-band UI moves to **1.4**.

---

## 2. Project Structure (actual, as of this entry)

```text
PAKWHEELS_DATA/
├─ pw_scraper.py            # Core library (~1.7k lines): robots guard, polite session,
│                           # parsers, catalogue, ETA, scrape loop, payload, exports, edits
├─ web_app.py               # Flask app, Job/JobManager, background scrape worker, REST API
├─ PW_SCRIPT.PY             # Thin CLI: --estimate, --catalogue, --self-test, --scatter
├─ config.json              # Catalogue cache + politeness defaults
├─ requirements.txt         # requests, bs4/soupsieve, flask, pandas, numpy, scipy
├─ README.md                # Install/run/API/compliance documentation
├─ DEVLOG.md                # This file
├─ SESSION-1-REPORT.md      # Baseline build + live verification notes
├─ SESSION-2-REPORT.md      # Manual-edit endpoint + test hardening notes
├─ templates/
│  └─ index.html            # Single-page UI: selection, ETA, progress, results, editor
├─ static/
│  ├─ app.js                # Fetch/poll, chart rendering, preview table, edit dialog
│  ├─ style.css             # UI styling
│  └─ vendor/plotly.min.js  # Local Plotly bundle (offline-safe)
├─ tests/
│  ├─ test_parsers.py       # 43 offline parser/robots/ETA/export/FMV/payload tests
│  └─ test_edits.py         # 27 endpoint/edit integration tests
├─ data/
│  └─ runs/<job_id>/        # listings.csv, listings.json, meta.json, scatter.html
└─ logs/
   └─ pakwheels.log         # Rotating application log
```

**Environment verified in this workspace:** Python 3.10.11; project venv at
`.venv` (Flask 3.1.3, pandas 2.3.3, numpy 2.2.6, scipy 1.15.3, soupsieve 2.10).
`requirements.txt` now declares pandas, numpy and scipy; soupsieve is capped below
3.0 because 3.x needs Python 3.11+.

---

## 3. Baseline Capabilities (do not regress)

- **Compliance:** every URL passes `assert_url_allowed()`; process-wide 2.0 s rate floor;
  retry/backoff on 429/5xx; blocked `sortby`/`nf`/`direct`/private paths.
- **Scrape flow:** estimate (ads, pages, latency, ETA) → background job → live progress →
  dedupe by `listing_id` → payload/preview/files.
- **Outputs:** CSV (UTF-8 BOM), JSON, standalone Plotly HTML, `meta.json`, plus
  `scatter.html` under `data/runs/<job_id>/`.
- **Manual corrections:** `POST /api/listings/<job_id>/edit`; 12 editable fields;
  `refresh_job_outputs()` rebuilds chart payload, preview and all on-disk exports so
  corrections never drift between views.
- **Tests:** last recorded run — 70 tests passing: 43 in `tests/test_parsers.py`
  (including 9 `FmvDealScoreTests` and 2 payload FMV contract tests) + 27 in
  `tests/test_edits.py` (2026-10-18).

---

## 4. Architecture Hooks for New Features

| Layer | File / symbol | Role for roadmap work |
|---|---|---|
| Analytics | `pw_scraper.py` → `linear_fit()`, `calculate_fmv_and_deal_scores()`, `build_scatter_payload()`, `_median()` | Existing stats/trend pattern; Feature 1 extends here |
| Job outputs | `web_app.py` → `refresh_job_outputs()` | **Single rebuild path** — any FMV/deal columns must be injected here so the chart, preview, CSV/JSON/HTML all agree |
| API | `web_app.py` → `/api/result/<job_id>`, `/api/download/<job_id>/<fmt>` | Payload and export extension points |
| Job state | `web_app.py` → `Job`, `JobManager` | In-memory only today; Feature 2 comparison needs multiple finished jobs, Feature 3 adds persistence |
| Chart | `static/app.js` → `drawChart()`, `renderResultStats()` | Deal-rating colouring and hover FMV; multi-trace overlay and curve overlays |
| Table | `static/app.js` → `renderPreview()`, `applyEditedPayload()` | Deal badges, FMV/residual columns, price-drop badges |
| Page | `templates/index.html` sections 1–4 | Sliders, comparison pickers, report button |
| Tests | `tests/test_parsers.py`, `tests/test_edits.py` | Offline, fixture-driven; new analytics tests follow the same style |

### Data contracts to preserve

- **Listing columns:** `listing_id, make, model, title, variant, variant_family, year,
  price_pkr, price_lacs, mileage_km, fuel_type, engine_cc, transmission, location,
  posted_text, is_featured, url` + Feature 1 fields `expected_price_pkr, residual_pkr,
  z_score, deal_rating, fmv_method`.
- **Payload keys:** `points, stats, facets, trend, editable_fields, meta`.
  Point keys now also include `expected_price_pkr, expected_price_lacs, residual_pkr,
  z_score, deal_rating, fmv_method`; `stats` adds `deal_counts, deal_rated_count,
  fmv_available_count`; `facets` adds `deal_rating`.
- **Meta keys:** `job_id, make, model, pages_requested, pages_available, pages_done,
  listings, delay_seconds, generated_at, source_url, estimate, duplicates_dropped,
  skipped_no_price, robots, manual_edits`.
- **Exports:** written only by `save_run()` + `save_scatter_html()`; filenames are
  consumed by `/api/download/<job_id>/<fmt>`.

---

## 5. Roadmap Breakdown

### Feature 1 — FMV & Deal Rating Engine

| ID | Micro-task | Target files | Status |
|---|---|---|---|
| 1.1 | Analytics engine + polynomial year→price fit (R², RMSE) + per-listing FMV/residual/z-score/deal labels + tests | `pw_scraper.py`, `tests/test_parsers.py` | **Done** |
| 1.2 | Payload + job-output wiring + UI deal surfacing: FMV point fields/stats, deal colouring, hover details, preview badges/filter, stats cards | `pw_scraper.py`, `web_app.py`, `templates/index.html`, `static/app.js`, `static/style.css`, tests | **Done** |
| 1.3 | Exponential depreciation fit (log-linear OLS) + model selection (poly vs exp by RMSE/AIC) | `pw_scraper.py`, tests | Not started |
| 1.4 | UI: fitted curve overlay + confidence band (deal badges/stats already shipped in 1.2) | `static/app.js`, `templates/index.html`, `static/style.css` | Not started |

Decisions locked in 1.1: grouping key is `make + model + variant` (falling back to
`variant_family` when `variant` is blank); minimum sample is 5 rows with ≥ 3 distinct
model years; z-score thresholds are −1.2 / −0.5 / 0.8; model-year and whole-model
medians are the fallbacks. Still open for 1.3: polynomial vs exponential selection
criterion and whether to trim IQR/MAD outliers before fitting.

### Feature 2 — Multi-Model Comparative Visualizations

| ID | Micro-task | Target files | Status |
|---|---|---|---|
| 2.1 | Support Multi-Model Scrape & Payload Generation (targets parameter, make_model stamping, meta.json labeling, tests) | `pw_scraper.py`, `web_app.py`, `static/app.js`, `templates/index.html`, tests | **Done** |
| 2.2 | Multi-trace price overlay with per-model colour/legend | `static/app.js`, UI controls | Not started |
| 2.3 | City-wise boxplots per model (location facet, outlier handling) | `static/app.js`, payload facets | Not started |
| 2.4 | Comparison summary stats + export snapshot | `web_app.py`, `static/app.js`, tests | Not started |

### Feature 3 — Historical Tracking & Price Drops

| ID | Micro-task | Target files | Status |
|---|---|---|---|
| 3.1 | SQLite schema (`data/market.db`): runs, listings, snapshots; idempotent migration | new `storage.py`, tests | Not started |
| 3.2 | Persist every completed run; snapshot comparison (new / removed / price changed) | `web_app.py`, `storage.py`, tests | Not started |
| 3.3 | Price history API + sparkline + price-drop badge in preview | `web_app.py`, `static/app.js`, UI | Not started |
| 3.4 | Re-scrape/refresh flow, retention policy, history export | `web_app.py`, `storage.py`, docs | Not started |

### Feature 4 — Advanced Filters & PDF Market Report

| ID | Micro-task | Target files | Status |
|---|---|---|---|
| 4.1 | Server-side filter model (price/year/mileage/engine/fuel/transmission/city) + tests | `pw_scraper.py`, `web_app.py`, tests | Not started |
| 4.2 | UI sliders/multi-selects, live counts, chart + preview sync | `templates/index.html`, `static/app.js`, `static/style.css` | Not started |
| 4.3 | PDF report: summary, charts, FMV/deal table, methodology, timestamp | new `report.py`, tests | Not started |
| 4.4 | Download endpoint + filtered export parity + docs | `web_app.py`, `templates/index.html`, README | Not started |

**Open decision (4.3):** `ReportLab` (pure Python, reliable on Windows) vs `WeasyPrint`
(HTML/CSS fidelity, requires GTK/Pango on Windows). Default recommendation: ReportLab
unless HTML-template fidelity is a hard requirement.

---

## 6. Working Conventions

1. **Micro-task discipline:** one action per turn; no future-feature code before its task.
2. **Single output rebuild path:** any derived column must be produced before/inside
   `refresh_job_outputs()` so preview, chart, CSV, JSON and HTML stay consistent.
3. **Deterministic tests, no network:** use synthetic DataFrames or saved fixtures.
4. **Backward compatibility:** preserve existing payload keys, listing columns and
   endpoint shapes; additive changes only unless a migration is documented here.
5. **Compliance is non-negotiable:** never lower the 2.0 s delay or add blocked params.
6. **Dependencies:** declare every new import in `requirements.txt`; numpy and scipy
   are now required by the analytics engine, and soupsieve stays below 3.0 for Python 3.10.
7. **Per-turn close-out:** update the status board, session log and next step below.

---

## 7. Commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v   # project venv (70 tests)
python web_app.py                              # UI at http://127.0.0.1:5000/
python PW_SCRIPT.PY --estimate --make honda --model civic
python PW_SCRIPT.PY --make honda --model civic --pages 2
python -m pytest tests -q                      # full suite
python -m unittest discover -s tests -v        # stdlib runner
node --check static/app.js                     # JS syntax check
```

---

## 8. Session Log

### 2026-10-18 — Project initialization
- Created `DEVLOG.md` with project map, baseline capabilities, architecture hooks,
  data contracts, four-feature roadmap and micro-task breakdown.
- No application source files modified; no runtime state changed.
- Status set to `Feature 1 - Micro-Task 1.1: Pending`.
- Verified workspace layout, entry points and environment versions (Python 3.10.11,
  Flask 3.1.3, pandas 2.3.3, numpy 2.2.6).
- Next: **Micro-Task 1.1** — analytics scaffold + polynomial fit + offline tests.

### 2026-10-18 — Micro-Task 1.1 complete (FMV & deal scores)
- Added `calculate_fmv_and_deal_scores()` to `pw_scraper.py`: per-variant quadratic
  year→price fit (`scipy.linalg.lstsq`, centred/scaled year), `expected_price_pkr`,
  `residual_pkr`, `z_score`, `deal_rating`, `fmv_method`; model-year / whole-model
  median fallbacks and `Unknown` for unusable prices.
- Added public `FMV_COLUMNS` and appended it to `LISTING_COLUMNS` so previews and
  CSV/JSON exports carry the fields; `refresh_job_outputs()` wiring followed in the
  re-scoped 1.2.
- Added `FmvDealScoreTests` (9 tests): exact quadratic recovery, z-threshold table,
  great/overpriced outliers, <5-row and single-year fallbacks, missing price/year
  columns, index preservation and idempotency.
- Created `.venv`, pinned `numpy 2.2.6`, `pandas 2.3.3`, `scipy 1.15.3`,
  `soupsieve 2.10` (3.x requires Python 3.11+); declared dependencies in
  `requirements.txt`.
- Verification: `python -m unittest discover -s tests -v` → **67 tests, OK**.
- Next: **Micro-Task 1.2** — exponential depreciation fit + model selection.

### 2026-10-18 — Micro-Task 1.2 complete (deal ratings exposed in payload + UI)
- `build_scatter_payload()` adds `expected_price_pkr`, `expected_price_lacs`,
  `residual_pkr`, `z_score`, `deal_rating`, `fmv_method` to every point, plus
  `stats.deal_counts`, `stats.deal_rated_count`, `stats.fmv_available_count` and
  `facets.deal_rating`. When the input frame has no computed scores, the payload
  builder runs `calculate_fmv_and_deal_scores()` itself.
- `refresh_job_outputs()` now computes FMV once before payload/preview/exports, so
  the chart, preview table, CSV, JSON and standalone HTML all carry the same deal
  data; manual corrections trigger a full recalculation.
- UI: chart “Colour by → Deal rating” with green/grey/red palette, hover FMV,
  residual, z-score and fit method; preview FMV/residual columns and colour-coded
  deal badges; deal-rating dropdown filters the preview; Great deals, Overpriced
  and FMV coverage stats cards. Standalone HTML shares the same colours/hover.
- Added 3 tests: payload FMV fields/summary, precomputed-FMV reuse, and
  `refresh_job_outputs()` payload/preview/CSV consistency.
- Verification: **70 tests OK** (43 parser + 27 web/edit), `py_compile` clean,
  `node --check static/app.js` clean, Flask `/` → 200, Playwright smoke test found
  all deal options in both dropdowns and no console errors.
- Re-scoped at user request: exponential fit/model selection moves to 1.3, curve
  overlay + confidence band to 1.4.
- Next: **Micro-Task 2.1** — support multi-model scrape & payload generation.

### 2026-10-19 — Micro-Task 2.1 complete (Multi-model scrape & payload generation)
- Updated `pw_scraper.py`: `scrape_pakwheels_model()`, `estimate_scrape()`, and `scrape_pakwheels()` accept target lists (e.g. `[("honda", "civic"), ("toyota", "corolla")]`) and delegate to `scrape_pakwheels_targets()`.
- Updated `build_scatter_payload()`: retains distinct `make`, `model`, and `make_model` identifiers per listing. Multi-model jobs set `meta.comparison = True`, populate `meta.make_model` headline, `meta.make_models`, `meta.make_model_counts`, `meta.targets`, and expose `facets.make_model`.
- Dataset export parity: `save_run()` and `merge_run_meta()` ensure `make_model` column is present and filled in both `listings.csv` and `listings.json`, and `meta.json` accurately describes comparison targets and counts.
- UI & Visualizer: added `<option value="make_model">Model</option>` to `templates/index.html`. `app.js` automatically defaults chart color to `make_model` for comparison runs. Standalone HTML export (`render_scatter_html`) defaults to grouping traces by `make_model`.
- CLI: `PW_SCRIPT.PY --scatter` updated to pass targets to `build_scatter_payload()`.
- Unit tests: added `MultiModelPayloadTests` (7 tests) in `tests/test_parsers.py`.
- Verification: **77 tests OK** (50 parser + 27 web/edit), `py_compile` clean, `node --check static/app.js` clean.
- Next: **Micro-Task 2.2** — multi-trace price overlay with per-model colour/legend.

---

## 9. Next Step

**Micro-Task 2.2** — multi-trace price overlay with per-model colour palette and
interactive Plotly legend filtering.




# PakWheels Market Explorer

A **local Flask web UI** for exploring used-car prices on PakWheels:

1. pick a **make** and **model** (catalogue read from the public `/used-cars/` page),
2. see a **measured estimate** of how long the scrape will take (ads found, pages available, latency, ETA range),
3. run the scraper with **live progress** (page x/y, listings, elapsed, remaining),
4. explore the results in an **interactive Plotly scatter chart** (price vs. year / mileage / engine,
   coloured by variant / transmission / fuel / city / deal rating) with FMV and deal details on
   hover, filter the preview by deal rating, and download **CSV / JSON / standalone HTML**.

Everything is intentionally polite and compliant with the site's `robots.txt`.

---

## 1. Install

```powershell
python -m pip install -r requirements.txt
```

Python 3.10+ is required (`requests`, `beautifulsoup4`, `flask`, `pandas`, `numpy`, `scipy`).
`lxml` is optional — if it is missing the parser silently falls back to the stdlib parser.

## 2. Run the web UI

```powershell
python web_app.py                 # opens http://127.0.0.1:5000/ in your browser
python web_app.py --port 8000 --no-browser
```

Then: **Estimate time** → **Start scrape** → watch progress → explore the chart and download the data.

## 3. Run the scraper from the command line

```powershell
python PW_SCRIPT.PY --estimate --make honda --model civic      # just measure + ETA
python PW_SCRIPT.PY --make honda --model civic --pages 2       # scrape + CSV
python PW_SCRIPT.PY --make suzuki --model alto --pages all --out alto.csv --scatter alto.html
python PW_SCRIPT.PY --catalogue                                # refresh/print the make/model list
python PW_SCRIPT.PY --self-test                                # offline unit tests
python PW_SCRIPT.PY                                            # legacy default: civic + corolla, 2 pages
```

## 4. How the "estimated time" is computed

| Input | Where it comes from |
|---|---|
| Ads available | `"1 - 25 of 7998 Results"` text on page 1 |
| Pages available | max of the `?page=N` links in the pagination widget and `ceil(ads / page size)` |
| Page size | measured - organic listings parsed on page 1 (25 on PakWheels) |
| Latency | wall-clock time of the page-1 request |
| ETA | `pages × (delay + latency + 0.15 s overhead)`; the UI shows a low/high range (×0.8 / ×1.5) |
| Finish time | current time + ETA |

The page-1 HTML fetched for the estimate is cached (5 min) and **reused by the scrape job**, so
you never pay for the same page twice.

## 5. Politeness and robots.txt compliance

* **One request at a time, ≥ 2.0 s apart.** `RateLimiter` holds a process-wide lock, so the UI
  preview and a running job can never produce a burst. The floor cannot be configured away.
* **Retry/backoff** on `429/500/502/503/504` (5 s → 15 s → 45 s, honouring `Retry-After`).
* **robots.txt guard.** Every outbound URL passes `assert_url_allowed()` before it is requested:
  * blocked query parameters: `sortby`, `nf`, `direct`, `seller_contact_info`
  * blocked path prefixes: `/login`, `/logout`, `/signup`, `/admin`, `/users`, `/sessions`,
    `/alerts`, `/shops/`, `/dealer/`, `/pictures`, `/attachment_files`, `/cgi-bin`,
    `/used-cars/lightbox_window`, `/used-cars/notify_seller`, `/used-cars/manage/`,
    `/used-cars/activate/`, `/fetch_*_featured_ads`, and more (see `pw_scraper.py`)
  * blocked fragments: `seller_contact_info`, `lightbox_window`, `*.json`
  * off-site hosts and non-HTTP schemes are rejected outright
* Featured-ad links that carry `?nf=true` have the query stripped automatically, and featured ads
  are **de-duplicated** by `listing_id` against the organic results.
* Only public listing pages are read — no login, no seller contact info, no forms.

### Catalogue discovery

The make/model list is read from the carousel `#browse-by-brand_used-cars` on
`https://www.pakwheels.com/used-cars/`, where links come in two shapes:

```
/used-cars/honda/14            ->  make   (slug = honda)
/used-cars/honda-civic/642     ->  model  (slug = honda-civic, make prefix stripped -> civic)
```

The parser is order-aware (a link whose slug starts with the current make slug + `-` is a model of
that make), ignores the used-bike carousel, and records `slug`, `full_slug` and `path` per model.
The search URL built from a model uses the verified form
`/used-cars/search/-/mk_<make>/md_<model-slug>/`, which was checked against the live site for
`honda/civic`, `suzuki/mehran` (5519 ads) and `toyota/land-cruiser` (1062 ads) — including
hyphenated model slugs.

## 6. Files

| File | Purpose |
|---|---|
| `pw_scraper.py` | Core library: rate limiter, robots guard, session, parsers, catalogue, ETA, scraper, exports |
| `web_app.py` | Flask app: REST API + job manager (one scrape at a time) + `main()` entry point |
| `templates/index.html` | Single-page UI (Jinja template) |
| `static/app.js` | Front-end controller (fetch, progress polling, Plotly chart, tables) |
| `static/style.css` | Styling |
| `static/vendor/plotly.min.js` | Optional local Plotly build (CDN fallback if absent) |
| `PW_SCRIPT.PY` | Thin CLI wrapper (`--estimate`, `--catalogue`, `--self-test`, scraping) |
| `tests/test_parsers.py` | 31 offline unit tests (fixtures mirror the live markup) |
| `config.json` | Defaults + cached make/model catalogue (auto-refreshed after 7 days) |
| `data/runs/<job_id>/` | Saved results: `listings.csv`, `listings.json`, `meta.json`, `scatter.html` |
| `logs/pakwheels.log` | Rotating log file (5 MB total, 3 backups) — the app logs instead of printing |

## 7. HTTP API

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | The web UI |
| `/api/catalogue` | GET | `{makes: [{name, slug, models: [...]}], source, built_at}` (`?refresh=1` to refetch) |
| `/api/estimate` | POST | `{make, model, pages, delay}` → `{total_results, available_pages, pages_to_scrape, latency_s, seconds_per_page, eta_seconds, eta_range_human, samples, notes}` |
| `/api/scrape` | POST | Same body (+ optional `pages_available`, `estimate`) → 202 with a job snapshot (409 if a job is running) |
| `/api/job/<id>` | GET | Live snapshot: state, progress %, pages, listings, elapsed, ETA, last 40 log events, `?log=N` |
| `/api/jobs` | GET | Recent jobs (`?log` not applicable) |
| `/api/result/<id>` | GET | Plotly payload: `{points, stats, facets, trend, editable_fields, meta}`; points carry FMV/deal fields |
| `/api/download/<id>/<fmt>` | GET | `csv`, `json`, `html` (standalone chart), `meta` |
| `/api/cancel/<id>` | POST | Cooperative cancel (stops after the current page) |
| `/api/health` | GET | Liveness + politeness settings + robots summary |

## 8. Scraped columns

`listing_id, make, model, title, variant, variant_family, year, price_pkr, price_lacs,
mileage_km, fuel_type, engine_cc, transmission, location, posted_text, is_featured, url`
plus Feature 1 analytics: `expected_price_pkr, residual_pkr, z_score, deal_rating, fmv_method`.

Notes:
* `variant` is derived from the title (`VTi Oriel Automatic 1.6`), `variant_family` collapses it
  (`VTi Oriel`) for chart grouping.
* `deal_rating` is `Great Deal` / `Good Deal` / `Fair Price` / `Overpriced` / `Unknown`; the
  UI can colour points and filter the preview by it, and the standalone HTML uses the same
  green-to-red palette.
* Cards with no usable price (`Price on Call`) are skipped and counted in `skipped_no_price`.
* Rows are de-duplicated on `listing_id`; repeats (featured vs. organic) are reported as
  `duplicates_dropped` in the job snapshot and `meta.json`.

## 9. Tests

```powershell
python -m unittest discover -s tests -v     # 70 tests, no network access
python PW_SCRIPT.PY --self-test             # same suite via the CLI
```

The fixtures reproduce the real DOM (`li.classified-listing`, `a.car-name`, `div.price-details`,
`ul.search-vehicle-info`, `ul.search-vehicle-info-2`), so the parsers are regression-tested even
when the network is unavailable.

## 10. Configuration

`config.json`:

```json
{
  "default_delay_seconds": 2.0,
  "default_pages": 5,
  "max_pages_cap": 20,
  "catalogue_ttl_days": 7,
  "catalogue": { "built_at": "...", "source": "live", "makes": [ ... ] }
}
```

`default_delay_seconds` can be raised but never lowered below the hard 2.0 s floor.
`max_pages_cap` limits a single run (20 pages ≈ 500 listings ≈ 1-2 minutes).

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| "Catalogue unavailable" in the UI | Check your connection, then press **Refresh catalogue**; you can also type make/model slugs manually (e.g. `mg` / `hs`). |
| Chart is blank | Hover the chart area; if Plotly could not load, use the **standalone chart** download or drop `plotly.min.js` into `static/vendor/`. |
| `429`/`503` responses | The client already backs off; raise the delay field (e.g. 3 s) and retry later. |
| Empty CSV | Older versions of `PW_SCRIPT.PY` used the wrong selector (`ul.search-title`) and produced zero rows. The current parsers read `ul.search-vehicle-info-2` — run `python -m unittest discover -s tests` to confirm. |
| Port 5000 busy | `python web_app.py --port 8000` |
| A job seems stuck | Press **Cancel**; the scraper stops after the current page, so it releases the slot within one request cycle. |

## 12. Limitations

* Results reflect what PakWheels returns for the first N search-result pages, in default (unsorted)
  order — `sortby` is disallowed by robots.txt, so no sorting/filtering parameters are used.
* Prices are converted to PKR numbers; listings without a numeric price are skipped.
* Listings with no `ul.search-vehicle-info-2` block fall back to the year parsed from the title, so a
  few rows may have an empty mileage/engine/transmission.
* The app keeps jobs in memory: restarting the server clears job state, but every finished run stays
  on disk under `data/runs/`.


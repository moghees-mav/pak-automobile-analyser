# TASK: Initialize Feature Roadmap & Development Log (DEVLOG.md)

You are an expert Python data engineer and full-stack developer working on the PakWheels Scraper and Visualizer workspace.

## 1. Operating Rules & Token Discipline
- **Token Efficiency:** Keep code explanations concise. Focus on clean code implementation and direct file updates.
- **Micro-Task Focus:** Complete ONLY the single requested micro-task per turn. Do not attempt future steps early.
- **Log Maintenance:** Maintain a `DEVLOG.md` file in the project root to record completed tasks, modified files, state changes, and next steps for future sessions.

## 2. Target Features Breakdown (Roadmap)
1. **Feature 1: FMV & Deal Rating Engine** (Polynomial/Exponential Curve Fitting, Residuals, Z-Scores)
2. **Feature 2: Multi-Model Comparative Visualizations** (Multi-trace overlay, City-wise Boxplots)
3. **Feature 3: Historical Tracking & Price Drops** (SQLite Storage, Snapshot Comparisons, Price Drop Badging)
4. **Feature 4: Advanced Filters & PDF Market Report** (UI Sliders, WeasyPrint/ReportLab PDF Generation)

---

## ACTION REQUIRED NOW:
1. Create a `DEVLOG.md` file in the workspace root with the initial project structure, roadmap, and current status set to `Feature 1 - Micro-Task 1.1: Pending`.
2. Confirm readiness to begin **Micro-Task 1.1**.
=======
=======
# TASK: Feature 1 - Micro-Task 1.1: Implement FMV Calculation Logic in pw_scraper.py

Refer to `DEVLOG.md`. We are implementing Feature 1, Micro-Task 1.1.

## Objectives
1. Add a function `calculate_fmv_and_deal_scores(df)` in `pw_scraper.py` using `scipy` or `statsmodels`.
2. For each vehicle variant with >= 5 data points:
   - Fit a polynomial/exponential model (`Model Year` vs. `Price`).
   - Calculate expected FMV price (`expected_price_pkr`) and residual (`price_pkr - expected_price_pkr`).
   - Compute standard deviation / Z-score per variant.
   - Assign a `deal_rating` string column: `Great Deal` (Z < -1.2), `Good Deal` (-1.2 <= Z < -0.5), `Fair Price` (-0.5 <= Z <= 0.8), or `Overpriced` (Z > 0.8).
3. Ensure missing/insufficient variant data falls back gracefully to standard model-level median.
4. Add unit tests for `calculate_fmv_and_deal_scores` in `tests/test_parsers.py`.
5. Update `DEVLOG.md` with progress and mark Micro-Task 1.1 complete.
=======
=======
# TASK: Feature 1 - Micro-Task 1.2: Expose Deal Ratings on UI and Plotly Scatter Plot

Refer to `DEVLOG.md`. Micro-Task 1.1 is complete.

## Objectives
1. Update `build_scatter_payload()` in `pw_scraper.py` to include `expected_price_lacs`, `residual_pkr`, and `deal_rating` in the chart payload and hover details.
2. Update Plotly marker coloring / trace options in `static/app.js` or server-side HTML generator to optionally color-code points by `deal_rating` (e.g., Green for Great Deal, Red for Overpriced).
3. Add a "Deal Rating" filter dropdown in `templates/index.html` and `static/app.js` to isolate specific bargain levels in the preview table.
4. Verify by running unit tests (`python -m unittest discover -s tests`).
5. Update `DEVLOG.md` with changes and mark Micro-Task 1.2 complete.
=======
=======
# TASK: Feature 2 - Micro-Task 2.1: Support Multi-Model Scrape & Payload Generation

Refer to `DEVLOG.md`. Feature 1 is complete. We are starting Feature 2.

## Objectives
1. Update `pw_scraper.py` scraping methods to accept a list of model targets (e.g., `[("honda", "civic"), ("toyota", "corolla")]`).
2. Update payload builders to maintain distinct `make_model` identifiers per listing so they render as separate color-coded traces in Plotly.
3. Ensure dataset exports (CSV/JSON) correctly label multi-model comparison jobs in `meta.json`.
4. Add unit test coverage for multi-model payload formatting.
5. Update `DEVLOG.md` and mark Micro-Task 2.1 complete.
=======
=======
# TASK: Feature 2 - Micro-Task 2.2: Add Multi-Model Overlay & City Boxplots to Frontend

Refer to `DEVLOG.md`. Micro-Task 2.1 is complete.

## Objectives
1. Update `static/app.js` to render multi-model datasets on a single scatter plot canvas with grouped legends and individual trendlines per model.
2. Add a new view tab or toggle in `templates/index.html` for **Regional Price Distribution (City-Wise Boxplots)**.
3. Use Plotly boxplot traces in `static/app.js` to render price distribution across top cities (Islamabad, Lahore, Karachi, Rawalpindi).
4. Verify layout responsiveness and chart toggles on `web_app.py`.
5. Update `DEVLOG.md` and mark Feature 2 as complete.
=======
=======
# TASK: Feature 3 - Micro-Task 3.1: Add SQLite Database Persistence Layer

Refer to `DEVLOG.md`. Feature 2 is complete. We are starting Feature 3.

## Objectives
1. Create a `storage.py` module to manage a local SQLite database (`data/pakwheels_history.db`).
2. Design schemas for:
   - `scrapes` (job_id, timestamp, make, model, listing_count)
   - `listings` (listing_id, title, year, price_pkr, mileage_km, location, fuel_type, scrape_id, first_seen, last_seen)
   - `price_history` (listing_id, price_pkr, recorded_at)
3. Add methods to insert scraped jobs, detect existing vs. new listings, and record price changes.
4. Add unit tests for database methods in `tests/test_storage.py`.
5. Update `DEVLOG.md` and mark Micro-Task 3.1 complete.
=======
=======
# TASK: Feature 3 - Micro-Task 3.2: Price Drop Detection & Historical Trends UI

Refer to `DEVLOG.md`. Micro-Task 3.1 is complete.

## Objectives
1. Hook `storage.py` into `pw_scraper.py` so completed scrapes write to SQLite automatically.
2. Implement price drop logic: Calculate `price_diff_pkr = current_price - initial_price`. Flag listings with price reductions.
3. Add a "Price Dropped" visual badge on the UI table and scatter plot hover card in `static/app.js`.
4. Add a summary metric card on the dashboard showing "Total Price Drops Detected".
5. Update `DEVLOG.md` and mark Feature 3 complete.
=======
=======
# TASK: Feature 4 - Micro-Task 4.1: Add Interactive Range Sliders to UI

Refer to `DEVLOG.md`. Feature 3 is complete. We are starting Feature 4.

## Objectives
1. Update `templates/index.html` and `static/style.css` to add controls for:
   - Year Range (Min Year – Max Year)
   - Price Range (Min Price – Max Price in Lakhs)
   - Mileage Threshold (Max KM)
2. Connect controls in `static/app.js` to dynamically re-filter the scatter plot, table preview, and summary stats in real time without re-scraping.
3. Ensure manual edits remain functional on filtered data subsets.
4. Update `DEVLOG.md` and mark Micro-Task 4.1 complete.
=======
=======
# TASK: Feature 4 - Micro-Task 4.2: Implement PDF Report Exporter

Refer to `DEVLOG.md`. Micro-Task 4.1 is complete.

## Objectives
1. Add a PDF generation module (`report_generator.py`) using `reportlab` or `weasyprint`.
2. Report structure:
   - **Header:** Make/Model summary, run date, total listings analyzed.
   - **Market Metrics Table:** Median price by year, average mileage, top bargain deals list.
   - **Embedded Charts:** Static export of the scatter plot & price distribution.
3. Expose a REST endpoint `GET /api/runs/<job_id>/download/pdf` in `web_app.py` and a "Download PDF Report" button on the UI.
4. Add unit test asserting PDF generation succeeds.
5. Update `DEVLOG.md`, mark Feature 4 complete, and record final project state.
=======
=======
### Role & Context
You are working as a lead developer on the `pak-automobile-analyser` project. We are pivoting from single-model trend checking to building a **Multi-Filter Deal Scanner & Arbitrage Identification Matrix** to spot under-priced cars (flipping candidates) across any brand, model, or custom filter set on PakWheels.

### Task Breakdown & Execution Plan

#### Task 4.1: Comprehensive PakWheels Search Filter Integration
* **Objective:** Expand the search URL builder and request parameters in `pw_scraper.py` and the Flask UI to support all major PakWheels search filters instead of requiring a single `make` and `model`.
* **Deliverables:**
  1. **Scraper Backend (`pw_scraper.py`):**
     * Update URL parameters to accept generic search flags:
       * Price range (`pr_min`, `pr_max`)
       * Model Year range (`yr_min`, `yr_max`)
       * Mileage range (`km_min`, `km_max`)
       * City / Location (`ct_<city>`)
       * Engine capacity range (`cc_min`, `cc_max`)
       * Transmission (`transmission_auto`, `transmission_manual`)
       * Fuel type (`fuel_petrol`, `fuel_diesel`, `fuel_hybrid`, `fuel_electric`)
       * Body type, seller type (Individual vs. Dealer), and color where available.
     * Ensure the path generator builds valid PakWheels search URLs combining active filters (e.g., `/used-cars/search/-/pr_2000000_3500000/yr_2015_2020/ct_islamabad/`).
  2. **Web UI (`templates/index.html`):**
     * Add clean form inputs (ranges, dropdowns, checkboxes) to allow users to run generic filter-based scrapes without selecting a strict Make/Model.

#### Task 4.2: Generic Search Scraper & Cross-Model Normalization
* **Objective:** Adapt the scraper parser and fair market value (FMV) math to handle mixed search results across multiple brands, models, and variants in a single scrape run.
* **Deliverables:**
  1. Group scraped listings dynamically by `(make, model, year, variant)` or `(make, model, year)`.
  2. Calculate group-level baseline Fair Market Value (FMV) and Z-Scores for each group containing sufficient data points ($N \ge 3$).
  3. Derive arbitrage potential metrics per row:
     * `pkr_below_fmv` (FMV - Listing Price)
     * `pct_below_fmv` (% discount against group average)
     * `z_score` (identifying under-priced outliers where $Z < -1.0$)

#### Task 4.3: Arbitrage & Flip Opportunity Matrix UI
* **Objective:** Update the frontend dashboard (`app.js` and `templates/index.html`) to highlight immediate flip opportunities.
* **Deliverables:**
  1. **Flip Opportunity Score / Ranking:** Add a sorting option to the UI data table ("Highest Margin First", "Greatest Discount %").
  2. **Visual Deal Matrix:** Update the Plotly scatter plot / view to color-code deals by discount depth and group mixed models visually.
  3. **Deal Badges:** Add badges for high-value indicators (e.g., "Urgent Sale Candidate", "Location Arbitrage Potential", "High Margin").

#### Task 4.4: Export & Deal Alert Dashboard
* **Objective:** Upgrade CSV/JSON exports and single-page standalone HTML reports to include flipping analytics.
* **Deliverables:**
  1. Include all calculated arbitrage fields (`fmv`, `pkr_below_fmv`, `pct_below_fmv`, `deal_rating`) in exported CSV/JSON files.
  2. Update standalone `scatter.html` export to display the Deal Rank and Flipping Matrix natively without needing the Flask backend.

---

### Execution Instructions & Rules

1. **Step-by-Step Execution:** Execute **Task 4.1 ONLY**. Do NOT write code or proceed to Tasks 4.2, 4.3, or 4.4 yet.
2. **Implementation Requirements:** Write modular, clean Python/JS code adhering to our rate-limiting, `robots.txt` compliance, and error-handling architecture.
3. **Mandatory Pause & Confirmation:** Upon finishing Task 4.1, provide a summary of the implemented code changes, modified files, and manual test steps. Then, **STOP** and ask me for confirmation to proceed to Task 4.2.
=======
=======

=======
=======
"""Local Flask web UI for the PakWheels used-car scraper.

Endpoints
---------
``GET  /``                      single-page UI (Jinja template)
``GET  /api/catalogue``         make/model catalogue (cached, ``?refresh=1`` to refetch)
``POST /api/estimate``          fetch page 1 once and return results count + ETA
``POST /api/scrape``            start a background scrape job (one at a time)
``GET  /api/job/<id>``          live progress snapshot (poll ~1 s)
``GET  /api/result/<id>``       Plotly-ready scatter payload
``POST /api/listings/<id>/edit`` apply hand corrections to one listing, refresh exports
``GET  /api/download/<id>/<f>`` CSV / JSON / HTML / meta download
``POST /api/cancel/<id>``       cooperative cancel
``GET  /api/health``            liveness + politeness settings

The scraper module enforces robots.txt compliance and the >=2 s politeness delay;
this layer never bypasses it.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template, request, send_file

import pw_scraper as pw

logger = pw.logger

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["JSON_SORT_KEYS"] = False
if hasattr(app, "json"):  # Flask >= 2.2 sorts keys in the JSON provider, not the config
    app.json.sort_keys = False

JOB_EVENT_LIMIT = 400
MAX_JOBS_KEPT = 25
ESTIMATE_CACHE_TTL_S = 300.0


@dataclass
class Job:
    """Everything the UI needs to know about one background scrape."""

    id: str
    make: str
    model: str
    pages: Optional[int]
    delay: float
    pages_available: Optional[int] = None
    #: Feature 2: every requested ``(make, model)`` slug pair; a single-model job
    #: holds one pair and behaves exactly as before.
    targets: list = field(default_factory=list)
    state: str = "pending"  # pending | running | done | error | cancelled
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    pages_done: int = 0
    pages_total: int = 0
    listings: int = 0
    duplicates_dropped: int = 0
    skipped_no_price: int = 0
    last_eta_remaining_s: float = 0.0
    eta_seconds: float = 0.0
    estimate: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    error: Optional[str] = None
    cancel_requested: bool = False
    payload: Optional[dict] = None
    preview: list = field(default_factory=list)
    files: dict = field(default_factory=dict)
    source_url: str = ""
    # -- multi-target progress bookkeeping (Feature 2) ----------------------- #
    target_pages: dict = field(default_factory=dict)  # index -> (pages_done, pages_total)
    target_listings: dict = field(default_factory=dict)  # index -> listings so far
    targets_done: int = 0
    # -- manual corrections (kept after the run so listings stay editable) -- #
    frame: Optional[object] = None
    meta: dict = field(default_factory=dict)
    run_dir: Optional[Path] = None
    title: str = ""
    manual_edits: list = field(default_factory=list)

    @property
    def target_pairs(self) -> list:
        """Requested ``(make, model)`` pairs (falls back to the primary pair)."""
        return list(self.targets) or [(self.make, self.model)]

    @property
    def is_comparison(self) -> bool:
        return len(self.target_pairs) > 1

    @property
    def make_model(self) -> str:
        """``"Honda Civic"`` for one model, ``"Honda Civic vs Toyota Corolla"`` for several."""
        return pw.comparison_label(self.target_pairs)

    @property
    def download_slug(self) -> str:
        """Filesystem-safe run name (``honda_civic_vs_toyota_corolla``)."""
        return re.sub(r"[^a-z0-9]+", "_", self.make_model.lower()).strip("_") or self.id

    # -- progress bookkeeping --------------------------------------------- #

    def ingest(self, event: dict) -> None:
        """Fold a scraper progress event into the job state."""
        stage = event.get("stage", "info")
        index = int(event.get("target_index") or 0)
        if index:
            # Several models in one job (Feature 2): progress is the sum over every
            # target seen so far, so the bar never jumps backwards between models.
            done = int(event.get("pages_done") or event.get("page") or 0)
            total = int(event.get("pages_total") or 0)
            prev_done, prev_total = self.target_pages.get(index, (0, 0))
            self.target_pages[index] = (max(prev_done, done), max(prev_total, total))
            self.pages_done = sum(pair[0] for pair in self.target_pages.values())
            self.pages_total = max(
                self.pages_total, sum(pair[1] for pair in self.target_pages.values())
            )
            if event.get("listings") is not None:
                previous = self.target_listings.get(index, 0)
                self.target_listings[index] = max(previous, int(event["listings"]))
                self.listings = sum(self.target_listings.values())
            if stage == "target_done":
                self.targets_done = max(self.targets_done, index)
        else:
            if event.get("pages_total"):
                self.pages_total = int(event["pages_total"])
            if event.get("page"):
                self.pages_done = int(event["page"])
            if event.get("pages_done"):
                self.pages_done = int(event["pages_done"])
            if event.get("listings") is not None:
                self.listings = int(event["listings"])
        if event.get("eta_remaining_s") is not None:
            self.last_eta_remaining_s = float(event["eta_remaining_s"])
        if event.get("duplicates_dropped") is not None:
            self.duplicates_dropped = int(event["duplicates_dropped"])
        if event.get("skipped_no_price") is not None:
            self.skipped_no_price = int(event["skipped_no_price"])
        if event.get("url") and not self.source_url:
            self.source_url = event["url"]

        self.events.append(
            {
                "at": datetime.now().strftime("%H:%M:%S"),
                "stage": stage,
                "message": self._describe(stage, event),
                "detail": {
                    key: event[key]
                    for key in ("page", "attempt", "wait_s", "elapsed_s")
                    if key in event
                },
            }
        )
        if len(self.events) > JOB_EVENT_LIMIT:
            del self.events[: len(self.events) - JOB_EVENT_LIMIT]

    @staticmethod
    def _describe(stage: str, event: dict) -> str:
        if stage == "start":
            return f"Starting scrape of {event.get('pages_total')} page(s)"
        if stage == "target_start":
            return (
                f"Model {event.get('target_index')}/{event.get('targets_total')}: "
                f"scraping {event.get('pages_total')} page(s)"
            )
        if stage == "target_done":
            return (
                f"Model {event.get('target_index')}/{event.get('targets_total')} done: "
                f"{event.get('listings', 0)} listings"
            )
        if stage == "page":
            return (
                f"Page {event.get('page')}/{event.get('pages_total')} parsed: "
                f"+{event.get('page_listings', 0)} listings "
                f"({event.get('listings', 0)} total)"
            )
        if stage == "retry":
            return (
                f"Retry {event.get('attempt')} for page {event.get('page')} "
                f"after {event.get('wait_s')}s ({event.get('message')})"
            )
        if stage == "error":
            return f"Error on page {event.get('page')}: {event.get('message')}"
        if stage == "stopped":
            return f"Stopped: {event.get('message')}"
        if stage == "done":
            return (
                f"Finished: {event.get('listings', 0)} listings in "
                f"{event.get('elapsed_s', 0)}s "
                f"({event.get('duplicates_dropped', 0)} duplicates removed)"
            )
        return str(event.get("message") or stage)

    # -- derived values ---------------------------------------------------- #

    @property
    def elapsed_s(self) -> float:
        started = self.started_at or self.created_at
        end = self.finished_at or time.time()
        return round(end - started, 1)

    @property
    def progress_pct(self) -> float:
        if self.state == "done":
            return 100.0
        if not self.pages_total:
            return 0.0
        return round(min(99.0, 100.0 * self.pages_done / self.pages_total), 1)

    def snapshot(self, log_limit: int = 25) -> dict:
        """JSON-safe view of the job for the polling UI."""
        return {
            "id": self.id,
            "make": self.make,
            "model": self.model,
            "make_model": self.make_model,
            "comparison": self.is_comparison,
            "targets": [
                {
                    "make": make,
                    "model": model,
                    "make_model": pw.make_model_label(make, model),
                }
                for make, model in self.target_pairs
            ],
            "targets_total": len(self.target_pairs),
            "targets_done": self.targets_done,
            "state": self.state,
            "pages_requested": self.pages,
            "pages_available": self.pages_available,
            "pages_done": self.pages_done,
            "pages_total": self.pages_total,
            "progress_pct": self.progress_pct,
            "listings": self.listings,
            "duplicates_dropped": self.duplicates_dropped,
            "skipped_no_price": self.skipped_no_price,
            "manual_edits": len(self.manual_edits),
            "elapsed_s": self.elapsed_s,
            "elapsed_human": pw.human_duration(self.elapsed_s),
            "eta_remaining_s": self.last_eta_remaining_s,
            "eta_remaining_human": (
                pw.human_duration(self.last_eta_remaining_s) if self.last_eta_remaining_s else ""
            ),
            "estimate": self.estimate,
            "events": self.events[-log_limit:],
            "error": self.error,
            "source_url": self.source_url,
            "downloads": (
                {
                    fmt: f"/api/download/{self.id}/{fmt}"
                    for fmt in ("csv", "json", "html", "meta")
                }
                if self.state == "done"
                else {}
            ),
            "result_url": f"/api/result/{self.id}" if self.state == "done" else None,
            "preview": self.preview if self.state == "done" else [],
            "stats": (self.payload or {}).get("stats") if self.payload else None,
        }


class JobManager:
    """In-memory job registry (single-process local app, one scrape at a time)."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.active_id: Optional[str] = None

    def create(
        self,
        make: str,
        model: str,
        pages: Optional[int],
        delay: float,
        pages_available: Optional[int],
        estimate: dict,
        targets: Optional[list] = None,
    ) -> Job:
        """Register a job for one model, or for a ``targets`` list (Feature 2)."""
        pairs = pw.normalize_targets(targets) if targets else [(make, model)]
        job = Job(
            id=uuid.uuid4().hex[:12],
            make=pairs[0][0],
            model=pairs[0][1],
            targets=pairs,
            pages=pages,
            delay=max(float(delay), pw.MIN_DELAY),
            pages_available=pages_available,
            estimate=estimate or {},
        )
        with self._lock:
            self._jobs[job.id] = job
            self._prune_locked()
        logger.info(
            "Job %s created (%s, pages=%s)", job.id, job.make_model, pages
        )
        return job

    def _prune_locked(self) -> None:
        if len(self._jobs) <= MAX_JOBS_KEPT:
            return
        ordered = sorted(self._jobs.values(), key=lambda job: job.created_at)
        for stale in ordered[: len(self._jobs) - MAX_JOBS_KEPT]:
            if stale.state in ("done", "error", "cancelled"):
                self._jobs.pop(stale.id, None)

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 10) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
        return jobs[:limit]

    def claim(self, job_id: str) -> bool:
        """Reserve the single scrape slot for *job_id*."""
        with self._lock:
            if self.active_id and self._jobs.get(self.active_id, None) is not None:
                active = self._jobs[self.active_id]
                if active.state in ("pending", "running"):
                    return False
            self.active_id = job_id
            return True

    def release(self, job_id: str) -> None:
        with self._lock:
            if self.active_id == job_id:
                self.active_id = None

    def active_job(self) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(self.active_id) if self.active_id else None
            if job and job.state in ("pending", "running"):
                return job
            return None

    def request_cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job or job.state not in ("pending", "running"):
            return False
        job.cancel_requested = True
        logger.info("Cancellation requested for job %s", job_id)
        return True


manager = JobManager()

#: Page-1 HTML fetched by /api/estimate, reused by the scrape job (one request less).
_estimate_cache: dict[tuple[str, str], tuple[float, pw.FetchResult]] = {}
_estimate_cache_lock = threading.Lock()


def cache_first_page(make: str, model: str, result: pw.FetchResult) -> None:
    with _estimate_cache_lock:
        _estimate_cache[(make, model)] = (time.time(), result)


def cached_first_page(make: str, model: str) -> Optional[pw.FetchResult]:
    with _estimate_cache_lock:
        entry = _estimate_cache.get((make, model))
    if not entry:
        return None
    cached_at, result = entry
    if time.time() - cached_at > ESTIMATE_CACHE_TTL_S:
        return None
    return result


def refresh_job_outputs(job: Job) -> None:
    """Rebuild the payload, preview and run files from ``job.frame``.

    Called once when a scrape finishes and again after every manual correction, so
    the chart, the preview table and the CSV/JSON/HTML downloads always agree.
    """
    frame = job.frame
    if frame is None:  # pragma: no cover - defensive
        raise ValueError("This job has no results to refresh.")

    # Feature 1: derive FMV/residual/z-score/deal columns once here. The payload,
    # preview table and every export are then built from the same analysed frame.
    frame = pw.calculate_fmv_and_deal_scores(frame)

    pairs = job.target_pairs
    title = job.title or f"PakWheels: {pw.comparison_label(pairs)}"
    meta = dict(job.meta)
    meta["listings"] = int(len(frame))
    meta["manual_edits"] = list(job.manual_edits)
    meta.setdefault("targets", [list(pair) for pair in pairs])

    job.payload = pw.build_scatter_payload(
        frame,
        make=job.make,
        model=job.model,
        targets=pairs,
        extra={
            "generated_at": meta.get("generated_at"),
            "source_url": meta.get("source_url"),
            "pages_scraped": job.pages_done,
            "job_id": job.id,
            "manual_edits": len(job.manual_edits),
            "targets": [list(pair) for pair in pairs],
        },
    )
    job.preview = pw.dataframe_preview(frame, limit=25)
    job.meta = meta

    run_dir = job.run_dir or pw.new_run_dir(job.id)
    job.run_dir = run_dir
    paths = pw.save_run(frame, meta, run_dir)
    paths["html"] = str(pw.save_scatter_html(job.payload, run_dir / "scatter.html", title))
    job.files = paths


def run_job(job: Job) -> None:
    """Background worker: scrape, analyse, persist the run, record the outcome."""
    job.state = "running"
    job.started_at = time.time()
    session = pw.PoliteSession(delay=job.delay)
    pairs = job.target_pairs
    title = f"PakWheels: {pw.comparison_label(pairs)}"
    # Page 1 fetched by /api/estimate is reused per target (one request less each).
    first_pages = {}
    for pair in pairs:
        cached = cached_first_page(*pair)
        if cached is not None:
            first_pages[pair] = cached

    try:
        frame = pw.scrape_pakwheels_targets(
            pairs,
            pages=job.pages,
            delay=job.delay,
            session=session,
            progress_cb=job.ingest,
            should_stop=lambda: job.cancel_requested,
            first_pages=first_pages,
            pages_available=job.pages_available if len(pairs) == 1 else None,
        )

        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        meta = {
            "job_id": job.id,
            "make": job.make,
            "model": job.model,
            "targets": [list(pair) for pair in pairs],
            "pages_requested": job.pages,
            "pages_available": job.pages_available,
            "pages_done": job.pages_done,
            "listings": int(len(frame)),
            "delay_seconds": job.delay,
            "generated_at": generated_at,
            "source_url": job.source_url
            or (pw.search_url(*pairs[0]) if len(pairs) == 1 else None),
            "estimate": job.estimate,
            "duplicates_dropped": job.duplicates_dropped,
            "skipped_no_price": job.skipped_no_price,
            "robots": "https://www.pakwheels.com/robots.txt",
        }

        job.frame = frame
        job.title = title
        job.run_dir = pw.new_run_dir(job.id)
        job.meta = meta
        refresh_job_outputs(job)
        job.state = "done"
        logger.info("Job %s finished with %d listings", job.id, len(frame))
    except pw.JobCancelled:
        job.state = "cancelled"
        job.ingest({"stage": "stopped", "message": "Cancelled by user"})
        logger.info("Job %s cancelled", job.id)
    except Exception as exc:  # noqa: BLE001 - surfaced in the UI instead of crashing
        logger.exception("Job %s failed", job.id)
        job.state = "error"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        session.close()
        job.finished_at = time.time()
        manager.release(job.id)


def start_job(job: Job) -> Job:
    """Run an already-created (and claimed) job on a daemon thread."""
    thread = threading.Thread(target=run_job, args=(job,), name=f"scrape-{job.id}", daemon=True)
    thread.start()
    return job


# --------------------------------------------------------------------------- #
# Request helpers
# --------------------------------------------------------------------------- #

PAGES_ALL = ("all", "none", "max", "")


def api_error(message: str, status: int = 400, **extra):
    """Uniform JSON error response (extra keys are merged into the body)."""
    payload = {"error": message}
    payload.update(extra)
    return jsonify(payload), status


def _targets_from_data(data: dict) -> list[tuple[str, str]]:
    """Read one ``make``/``model`` pair or a ``targets`` list (Feature 2).

    ``targets`` may be a JSON list of ``{"make": ..., "model": ...}`` objects (or
    ``[make, model]`` pairs), the same list JSON-encoded in a form field, or the
    compact form ``"honda:civic; toyota:corolla"``. Everything is validated and
    de-duplicated by :func:`pw_scraper.normalize_targets` (max 4 models).
    """
    raw = data.get("targets")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raw = None
        else:
            try:
                raw = json.loads(text)
            except ValueError:
                raw = [part for part in re.split(r"[;,\n]+", text) if part.strip()]
    if raw is None or raw == [] or raw == "":
        raw = [{"make": data.get("make"), "model": data.get("model")}]
    if isinstance(raw, dict):
        raw = [raw]

    items = []
    for entry in raw:
        if isinstance(entry, str):
            make, _, model = entry.partition(":")
            entry = {"make": make, "model": model}
        if not isinstance(entry, dict):
            raise ValueError("Each target needs a 'make' and a 'model'.")
        items.append(entry)
    return pw.normalize_targets(items)


def _parse_request_payload() -> tuple[list[tuple[str, str]], Optional[int], float]:
    """Validate and normalise the JSON/form body sent by the UI.

    Returns ``(targets, pages, delay)`` where *targets* is a non-empty list of
    validated ``(make, model)`` slug pairs (one entry for the classic UI).
    """
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    targets = _targets_from_data(data)

    try:
        delay = max(float(data.get("delay") or pw.DEFAULT_DELAY), pw.MIN_DELAY)
    except (TypeError, ValueError):
        raise ValueError("'delay' must be a number of seconds.")

    raw_pages = data.get("pages", pw.DEFAULT_PAGES)
    if raw_pages is None or str(raw_pages).strip().lower() in PAGES_ALL:
        pages: Optional[int] = None
    else:
        try:
            pages = int(raw_pages)
        except (TypeError, ValueError):
            raise ValueError("'pages' must be a whole number, or 'all'.")
        if pages < 1:
            raise ValueError("'pages' must be at least 1.")
        pages = min(pages, pw.MAX_PAGES_CAP)

    return targets, pages, delay


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@app.get("/")
def index():
    config = pw.load_config()
    return render_template(
        "index.html",
        default_delay=max(
            float(config.get("default_delay_seconds", pw.DEFAULT_DELAY)), pw.MIN_DELAY
        ),
        default_pages=int(config.get("default_pages", pw.DEFAULT_PAGES)),
        max_pages=int(config.get("max_pages_cap", pw.MAX_PAGES_CAP)),
        min_delay=pw.MIN_DELAY,
        catalogue_source=(config.get("catalogue") or {}).get("source"),
        deal_ratings=pw.DEAL_RATING_ORDER,
    )


@app.get("/api/catalogue")
def api_catalogue():
    """Make/model catalogue (cached in config.json for 7 days by default)."""
    refresh = str(request.args.get("refresh", "")).lower() in ("1", "true", "yes")
    session = pw.PoliteSession()
    try:
        catalogue = pw.fetch_catalogue(session=session, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        logger.exception("Catalogue fetch failed")
        return api_error(f"Catalogue unavailable: {exc}", 502)
    finally:
        session.close()

    makes = catalogue.get("makes", [])
    return jsonify(
        {
            "makes": makes,
            "built_at": catalogue.get("built_at"),
            "source": catalogue.get("source", "cache"),
            "total_models": sum(len(make["models"]) for make in makes),
        }
    )


def _estimate_response(estimates: list) -> dict:
    """Combine one :class:`pw_scraper.Estimate` per target into the UI payload.

    A single-model job returns the same fields as before (so the existing UI keeps
    working), plus a one-entry ``targets`` list. Multi-model jobs sum pages, results
    and time so the pre-run ETA still describes the whole job (Feature 2).
    """
    primary = estimates[0].to_dict()
    targets = [estimate.to_dict() for estimate in estimates]
    total_pages = sum(estimate.pages_to_scrape for estimate in estimates)
    total_seconds = sum(estimate.eta_seconds for estimate in estimates)
    known_results = [e.total_results for e in estimates if e.total_results is not None]
    known_pages = [e.available_pages for e in estimates if e.available_pages is not None]
    now = datetime.now()

    payload = dict(primary)
    payload.update(
        {
            "make_model": pw.comparison_label(
                [(estimate.make, estimate.model) for estimate in estimates]
            ),
            "targets": targets,
            "target_count": len(estimates),
            "total_results": sum(known_results) if known_results else None,
            "available_pages": max(known_pages) if known_pages else None,
            "pages_to_scrape": total_pages,
            "eta_seconds": round(total_seconds, 1),
            "eta_low_s": round(total_seconds * 0.8, 1),
            "eta_high_s": round(total_seconds * 1.5, 1),
            "eta_human": pw.human_duration(total_seconds),
            "eta_range_human": (
                f"{pw.human_duration(total_seconds * 0.8)} - "
                f"{pw.human_duration(total_seconds * 1.5)}"
            ),
            "finish_at": now.timestamp() + total_seconds,
            "started_at_iso": now.strftime("%H:%M:%S"),
            "finish_at_iso": datetime.fromtimestamp(
                now.timestamp() + total_seconds
            ).strftime("%H:%M:%S"),
            "min_delay": pw.MIN_DELAY,
            "max_pages": pw.MAX_PAGES_CAP,
            "measured": all(estimate.measured for estimate in estimates),
            "notes": "; ".join(e.notes for e in estimates if e.notes),
        }
    )
    return payload


@app.post("/api/estimate")
def api_estimate():
    """Fetch page 1 per target and return the results count + a measured ETA."""
    try:
        targets, pages, delay = _parse_request_payload()
    except ValueError as exc:
        return api_error(str(exc))

    session = pw.PoliteSession(delay=delay)
    estimates = []
    try:
        for make, model in targets:
            url = pw.search_url(make, model)
            result = session.get(url)
            if result.status_code == 200:
                cache_first_page(make, model, result)  # reused by /api/scrape
            estimates.append(
                pw.estimate_scrape(
                    session=session,
                    make=make,
                    model=model,
                    max_pages=pages,
                    delay=delay,
                    sample_size=5,
                    first_page=result,
                )
            )
    except pw.RobotsDisallowed as exc:
        return api_error(f"Blocked by the robots.txt policy guard: {exc}", 403)
    except pw.FetchError as exc:
        return api_error(f"Could not reach PakWheels: {exc}", 502)
    finally:
        session.close()

    return jsonify(_estimate_response(estimates))


@app.post("/api/scrape")
def api_scrape():
    """Start a background scrape (only one job at a time - politeness first)."""
    try:
        targets, pages, delay = _parse_request_payload()
    except ValueError as exc:
        return api_error(str(exc))

    active = manager.active_job()
    if active:
        return api_error(
            f"Job {active.id} ({active.make_model}) is still running. "
            "Cancel it or wait for it to finish.",
            409,
            active_job=active.id,
        )

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    estimate = data.get("estimate") if isinstance(data.get("estimate"), dict) else {}

    pages_available: Optional[int] = None
    raw_available = data.get("pages_available")
    if raw_available not in (None, "", "null", "None"):
        try:
            pages_available = int(raw_available)
        except (TypeError, ValueError):
            pages_available = None
    if pages_available is not None and pages_available < 1:
        pages_available = None

    try:
        job = manager.create(
            targets[0][0],
            targets[0][1],
            pages,
            delay,
            pages_available if len(targets) == 1 else None,
            estimate,
            targets=targets,
        )
    except ValueError as exc:
        return api_error(str(exc))

    if len(targets) > 1:
        # Seed the planned page count so the progress bar covers the whole job.
        planned = [
            int(item.get("pages_to_scrape") or 0)
            for item in estimate.get("targets") or []
            if isinstance(item, dict)
        ]
        if planned:
            job.pages_total = max(job.pages_total, sum(planned))

    if not manager.claim(job.id):
        return api_error("Another scrape is already running.", 409)
    start_job(job)
    return jsonify(job.snapshot()), 202


@app.get("/api/job/<job_id>")
def api_job(job_id: str):
    job = manager.get(job_id)
    if not job:
        return api_error("Unknown job id.", 404)
    log_limit = request.args.get("log", default=25, type=int) or 25
    return jsonify(job.snapshot(log_limit=max(1, min(log_limit, 200))))


@app.get("/api/jobs")
def api_jobs():
    return jsonify(
        {
            "active": (manager.active_job().id if manager.active_job() else None),
            "jobs": [
                {
                    "id": job.id,
                    "make": job.make,
                    "model": job.model,
                    "make_model": job.make_model,
                    "comparison": job.is_comparison,
                    "state": job.state,
                    "listings": job.listings,
                    "created_at": datetime.fromtimestamp(job.created_at).strftime("%H:%M:%S"),
                    "result_url": f"/api/result/{job.id}" if job.state == "done" else None,
                }
                for job in manager.list(limit=10)
            ],
        }
    )


@app.get("/api/result/<job_id>")
def api_result(job_id: str):
    job = manager.get(job_id)
    if not job:
        return api_error("Unknown job id.", 404)
    if job.state != "done" or not job.payload:
        return api_error(f"Job is '{job.state}'; results are not ready yet.", 409)
    return jsonify(job.payload)


@app.post("/api/listings/<job_id>/edit")
def api_listing_edit(job_id: str):
    """Correct one listing's fields by hand, then refresh payload + exports.

    Body: ``{"listing_id": "...", "fields": {"price_pkr": 1250000, ...}}``.
    ``fields`` may also arrive as a JSON string or as flat form pairs
    (``listing_id=...&price_pkr=1250000``) so the endpoint stays curl-friendly.
    Only fields listed in :data:`pw_scraper.EDITABLE_FIELDS` are accepted; blank
    values clear a field and every change is appended to the run metadata.
    """
    job = manager.get(job_id)
    if not job:
        return api_error("Unknown job id.", 404)
    if job.state != "done" or job.frame is None:
        return api_error(
            f"Job is '{job.state}'; listings can only be corrected once a run has finished.",
            409,
        )

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    listing_id = str(data.get("listing_id") or "").strip()
    fields = data.get("fields")
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except ValueError:
            return api_error("'fields' must be a JSON object of field -> value.")
    if fields is None:
        # Form posts may pass the pairs flat: listing_id=...&price_pkr=1250000.
        fields = {key: value for key, value in data.items() if key != "listing_id"}
    if not listing_id:
        return api_error("'listing_id' is required.")
    if not isinstance(fields, dict) or not fields:
        return api_error("'fields' must be a non-empty object of field -> value.")

    try:
        frame, changes = pw.apply_listing_edit(job.frame, listing_id, fields)
    except ValueError as exc:
        return api_error(str(exc))

    if changes:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for change in changes:
            job.manual_edits.append({"at": stamp, "listing_id": listing_id, **change})
        job.frame = frame
        try:
            refresh_job_outputs(job)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI instead of crashing
            logger.exception("Could not refresh job %s after an edit", job.id)
            return api_error(f"Kept in memory but the exports could not be rewritten: {exc}", 500)
        logger.info(
            "Job %s: %d correction(s) applied to listing %s",
            job.id,
            len(changes),
            listing_id,
        )

    return jsonify(
        {
            "listing_id": listing_id,
            "changes": changes,
            "manual_edits": len(job.manual_edits),
            "payload": job.payload,
            "preview": job.preview,
        }
    )


@app.get("/api/download/<job_id>/<fmt>")
def api_download(job_id: str, fmt: str):
    job = manager.get(job_id)
    if not job or job.state != "done":
        return api_error("Result not available.", 404)

    fmt = fmt.lower()
    path = (job.files or {}).get(fmt)
    if not path or not Path(path).exists():
        return api_error(f"No '{fmt}' file for this job.", 404)

    mimetypes = {
        "csv": "text/csv",
        "json": "application/json",
        "meta": "application/json",
        "html": "text/html",
    }
    extension = "json" if fmt == "meta" else fmt
    return send_file(
        Path(path),
        mimetype=mimetypes.get(fmt, "application/octet-stream"),
        as_attachment=True,
        download_name=f"pakwheels_{job.download_slug}_{job.id}.{extension}",
    )


@app.post("/api/cancel/<job_id>")
def api_cancel(job_id: str):
    if not manager.request_cancel(job_id):
        return api_error("That job is not running.", 409)
    return jsonify({"cancelled": job_id})


@app.get("/api/health")
def api_health():
    active = manager.active_job()
    return jsonify(
        {
            "status": "ok",
            "min_delay_seconds": pw.MIN_DELAY,
            "default_delay_seconds": pw.DEFAULT_DELAY,
            "max_pages": pw.MAX_PAGES_CAP,
            "robots": {
                "url": "https://www.pakwheels.com/robots.txt",
                "blocked_query_params": sorted(pw.DISALLOWED_QUERY_KEYS),
                "blocked_path_prefixes": len(pw.DISALLOWED_PATH_PREFIXES),
            },
            "active_job": active.id if active else None,
            "runs_dir": str(pw.RUNS_DIR),
            "log_file": str(pw.LOG_FILE),
        }
    )


@app.after_request
def add_no_store_headers(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import webbrowser

    parser = argparse.ArgumentParser(
        description="Local web UI for the PakWheels used-car scraper."
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="port (default: 5000)")
    parser.add_argument("--debug", action="store_true", help="Flask debug mode")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args(argv)

    pw.setup_logging()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    url = f"http://{args.host}:{args.port}/"
    logger.info("PakWheels scraper UI listening on %s", url)
    logger.info(
        "Politeness: min %.1fs between requests, max %d pages per run",
        pw.MIN_DELAY,
        pw.MAX_PAGES_CAP,
    )
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True,
        use_reloader=False,  # reloading would kill running scrape jobs
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

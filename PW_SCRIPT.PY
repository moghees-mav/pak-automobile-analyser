"""Command-line wrapper around the PakWheels scraper (:mod:`pw_scraper`).

All the real logic (politeness, robots.txt guard, parsing, ETA, export) lives in
``pw_scraper.py``; this file is a thin, backward-compatible CLI:

    python PW_SCRIPT.PY                                   # honda/civic + toyota/corolla, 2 pages each
    python PW_SCRIPT.PY --make honda --model civic --pages 2
    python PW_SCRIPT.PY --make suzuki --model alto --pages all --out alto.csv
    python PW_SCRIPT.PY --estimate --make kia --model sportage
    python PW_SCRIPT.PY --catalogue
    python PW_SCRIPT.PY --self-test

Every request is spaced at least 2 s apart and validated against
https://www.pakwheels.com/robots.txt before it is sent.
"""

from __future__ import annotations

import argparse
import unittest
from pathlib import Path

import pandas as pd

import pw_scraper as pw

# Re-exported so older code that did ``from PW_SCRIPT import ...`` keeps working.
from pw_scraper import (  # noqa: F401
    parse_price,
    scrape_pakwheels_model,
    setup_logging,
)

DEFAULT_TARGETS = [("honda", "civic"), ("toyota", "corolla")]
DEFAULT_OUTPUT = "pakwheels_listings.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape PakWheels used-car listings politely (robots.txt aware).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--make", action="append", help="car make slug, repeatable")
    parser.add_argument("--model", action="append", help="car model slug, repeatable")
    parser.add_argument("--pages", default=str(pw.DEFAULT_PAGES),
                        help="pages per model, or 'all'")
    parser.add_argument("--delay", type=float, default=pw.DEFAULT_DELAY,
                        help=f"seconds between requests (minimum {pw.MIN_DELAY})")
    parser.add_argument("--out", default=DEFAULT_OUTPUT, help="CSV output path")
    parser.add_argument("--json", dest="json_out", help="optional JSON output path")
    parser.add_argument("--scatter", help="optional standalone HTML scatter output path")
    parser.add_argument("--estimate", action="store_true",
                        help="only measure the result count and ETA")
    parser.add_argument("--catalogue", action="store_true",
                        help="refresh and print the make/model catalogue")
    parser.add_argument("--self-test", action="store_true", help="run the offline unit tests")
    return parser


def parse_pages(value: str):
    if str(value).strip().lower() in ("all", "none", "max", ""):
        return None
    pages = int(value)
    if pages < 1:
        raise argparse.ArgumentTypeError("--pages must be >= 1")
    return min(pages, pw.MAX_PAGES_CAP)


def resolve_targets(args) -> list[tuple[str, str]]:
    makes = [item.lower() for item in (args.make or [])]
    models = [item.lower() for item in (args.model or [])]
    if not makes and not models:
        return list(DEFAULT_TARGETS)
    if makes and models:
        if len(makes) != len(models):
            raise SystemExit("--make and --model must be given the same number of times")
        return list(zip(makes, models))
    if models and not makes:
        raise SystemExit("--model requires a matching --make")
    return [(make, DEFAULT_TARGETS[0][1]) for make in makes]


def run_self_test() -> int:
    suite = unittest.defaultTestLoader.discover(str(pw.BASE_DIR / "tests"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


def run_catalogue() -> int:
    session = pw.PoliteSession()
    try:
        catalogue = pw.fetch_catalogue(session=session, refresh=True)
    finally:
        session.close()
    print(f"Catalogue source: {catalogue.get('source')} (built {catalogue.get('built_at')})")
    for make in catalogue.get("makes", []):
        models = ", ".join(model["slug"] for model in make["models"])
        print(f"  {make['slug']:16s} {models}")
    return 0


def estimate_target(make: str, model: str, pages, delay: float) -> int:
    session = pw.PoliteSession(delay=delay)
    try:
        estimate = pw.estimate_scrape(
            session=session, make=make, model=model, max_pages=pages, delay=delay
        )
    finally:
        session.close()

    total = estimate.total_results if estimate.total_results is not None else "unknown"
    available = estimate.available_pages if estimate.available_pages else "unknown"
    print(f"{make} {model}: {total} ads, {available} page(s) available, "
          f"scraping {estimate.pages_to_scrape} page(s)")
    print(f"  latency {estimate.latency_s}s/page, delay {estimate.delay}s -> "
          f"ETA {pw.human_duration(estimate.eta_seconds)} ({estimate.eta_range_human})")
    if estimate.notes:
        print(f"  note: {estimate.notes}")
    for row in estimate.samples[:3]:
        print(f"  sample: {row.get('year')} {row.get('title')} - {row.get('price_pkr')} PKR")
    return 0


def scrape_target(make: str, model: str, pages, delay: float) -> pd.DataFrame:
    session = pw.PoliteSession(delay=delay)

    def on_event(event: dict) -> None:
        stage = event.get("stage")
        if stage == "page":
            print(f"  page {event['page']}/{event['pages_total']}: "
                  f"+{event['page_listings']} listings ({event['listings']} total, "
                  f"{event['elapsed_s']}s elapsed)")
        elif stage == "retry":
            print(f"  retry {event['attempt']} for page {event['page']} in "
                  f"{event['wait_s']}s ({event['message']})")
        elif stage == "error":
            print(f"  error on page {event['page']}: {event['message']}")
        elif stage == "stopped":
            print(f"  stopped: {event['message']}")

    try:
        return pw.scrape_pakwheels_model(
            make=make, model=model, pages=pages, delay=delay,
            session=session, progress_cb=on_event,
        )
    finally:
        session.close()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    pw.setup_logging()

    if args.self_test:
        return run_self_test()
    if args.catalogue:
        return run_catalogue()

    pages = parse_pages(args.pages)
    delay = max(args.delay, pw.MIN_DELAY)
    targets = resolve_targets(args)

    if args.estimate:
        for make, model in targets:
            estimate_target(make, model, pages, delay)
        return 0

    frames = []
    for make, model in targets:
        print(f"Scraping {make} {model} (pages={pages if pages else 'all'}, delay={delay}s)...")
        frame = scrape_target(make, model, pages, delay)
        print(f"  -> {len(frame)} listings")
        frames.append(frame)

    if not frames:
        print("Nothing to save.")
        return 1

    full = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
    full.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"\nSaved {len(full)} listings to '{args.out}'.")

    if args.json_out:
        full.to_json(args.json_out, orient="records", indent=2, force_ascii=False)
        print(f"Saved JSON to '{args.json_out}'.")

    if args.scatter:
        payload = pw.build_scatter_payload(full, targets=targets)
        title = f"PakWheels: {pw.comparison_label(targets)}"
        pw.save_scatter_html(payload, Path(args.scatter), title)
        print(f"Saved interactive chart to '{args.scatter}'.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

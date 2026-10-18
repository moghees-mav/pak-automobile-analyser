"""PakWheels used-car scraper core.

Responsibilities
----------------
* Polite HTTP access (2.0 s minimum spacing between requests, retries/backoff,
  realistic headers, single reusable session).
* robots.txt compliance guard -- every outbound URL is validated *before* it is
  requested, so disallowed query parameters (``sortby=``, ``nf=true``, ...) and
  private endpoints (``/login``, ``/users``, ...) can never be hit by accident.
* HTML parsing for search result cards (year, price, mileage, fuel, engine CC,
  transmission, location, variant, ...).
* Make/model catalogue discovery from the public ``/used-cars/`` page.
* Scrape-time estimation (results count, available pages, ETA).
* Paginated scraping with progress callbacks + CSV/JSON/HTML export.

Nothing in this module performs authentication or touches seller contact data.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import math
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Union
from urllib.parse import parse_qsl, urlencode, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from scipy.linalg import lstsq

# --------------------------------------------------------------------------- #
# Paths & constants
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RUNS_DIR = DATA_DIR / "runs"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "pakwheels.log"
CONFIG_FILE = BASE_DIR / "config.json"

SITE_ROOT = "https://www.pakwheels.com"
SEARCH_PATH = "/used-cars/search/-/mk_{make}/md_{model}/"
CATALOGUE_PATH = "/used-cars/"

ALLOWED_HOSTS = ("www.pakwheels.com", "pakwheels.com")

DEFAULT_DELAY = 2.0
MIN_DELAY = 2.0
DEFAULT_PAGES = 5
MAX_PAGES_CAP = 20
PAGE_OVERHEAD_S = 0.15
CATALOGUE_TTL_DAYS = 7
RESULTS_PER_PAGE_HINT = 25
#: Multi-model jobs: comparison views are designed for 2-4 models per run.
MAX_TARGETS = 4

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# --------------------------------------------------------------------------- #
# robots.txt compliance guard
# --------------------------------------------------------------------------- #
# Derived verbatim from https://www.pakwheels.com/robots.txt (User-agent: *)
DISALLOWED_PATH_PREFIXES = (
    "/login",
    "/logout",
    "/signup",
    "/admin",
    "/users",
    "/sessions",
    "/alerts",
    "/shops/",
    "/dealer/",
    "/pictures",
    "/attachment_files",
    "/oauth",
    "/ckeditor",
    "/password_resets",
    "/get_cities",
    "/checkuser",
    "/checkemail",
    "/checkspam",
    "/subscribe_newsletter",
    "/report_abuse_histories",
    "/remove_picture/",
    "/multi_file_uploader/",
    "/recent-ads/",
    "/car_sure_requests/",
    "/newarrivals/",
    "/media/gallery_view/",
    "/cgi-bin",
    "/xt_details.asp",
    "/car-finance",
    "/1012037",
    "/wp-content/plugins/",
    "/forums/admin-cp",
    "/forums/usercp.php",
    "/forums/register",
    "/used-cars/lightbox_window",
    "/used-cars/notify_seller",
    "/used-cars/manage/",
    "/used-cars/activate/",
    "/used-bikes/lightbox_window",
    "/used-bikes/notify_seller",
    "/used-bikes/manage/",
    "/used-bikes/activate/",
    "/accessories/notify_seller",
    "/accessories/lightbox_window",
    "/fetch_used_cars_featured_ads",
    "/fetch_used_bikes_featured_ads",
    "/fetch_ad_listings_featured_ads",
)

# Query parameter names blocked by robots.txt (we never need any of them).
DISALLOWED_QUERY_KEYS = frozenset({"sortby", "nf", "direct", "seller_contact_info"})
DISALLOWED_PATH_FRAGMENTS = (
    "seller_contact_info",
    "lightbox_window",
    "increment_phone_count",
)


class RobotsDisallowed(RuntimeError):
    """Raised when a URL is disallowed by PakWheels' robots.txt."""


def is_url_allowed(url: str) -> bool:
    """Return ``True`` when *url* is safe (robots.txt compliant) to request."""
    try:
        assert_url_allowed(url)
    except RobotsDisallowed:
        return False
    return True


def assert_url_allowed(url: str) -> str:
    """Validate *url* against the robots.txt rules we are bound by.

    Raises :class:`RobotsDisallowed` instead of silently scraping something the
    site owner asked us not to. Returns the URL unchanged when it is allowed.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise RobotsDisallowed(f"Refusing non-HTTP URL: {url!r}")
    if parsed.netloc.lower() not in ALLOWED_HOSTS:
        raise RobotsDisallowed(f"Refusing off-site URL: {url!r}")

    path = parsed.path.lower()

    if path.endswith(".json"):
        raise RobotsDisallowed(f"robots.txt disallows .json endpoints: {url!r}")

    for fragment in DISALLOWED_PATH_FRAGMENTS:
        if fragment in path:
            raise RobotsDisallowed(
                f"robots.txt disallows path fragment {fragment!r}: {url!r}"
            )

    for prefix in DISALLOWED_PATH_PREFIXES:
        if path.startswith(prefix):
            raise RobotsDisallowed(
                f"robots.txt disallows path prefix {prefix!r}: {url!r}"
            )

    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in DISALLOWED_QUERY_KEYS:
            raise RobotsDisallowed(
                f"robots.txt disallows query parameter {key!r}: {url!r}"
            )

    return url


# --------------------------------------------------------------------------- #
# Logging (file + console, replaces print())
# --------------------------------------------------------------------------- #

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging(level: int = logging.INFO, log_file: Path = LOG_FILE) -> logging.Logger:
    """Configure the package logger exactly once (idempotent)."""
    logger = logging.getLogger("pakwheels")
    if getattr(logger, "_pw_configured", False):
        return logger

    logger.setLevel(level)
    logger.propagate = False
    formatter = logging.Formatter(LOG_FORMAT)

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:  # pragma: no cover - read-only filesystem fallback
        pass

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    logger._pw_configured = True  # type: ignore[attr-defined]
    return logger


logger = setup_logging()


# --------------------------------------------------------------------------- #
# Rate limiting + polite HTTP session
# --------------------------------------------------------------------------- #


class RateLimiter:
    """Process-wide limiter guaranteeing a minimum gap between requests.

    The lock is held while sleeping so concurrent callers are serialised: a
    background scrape plus a UI preview request can never produce a burst.
    """

    def __init__(self, min_delay: float = DEFAULT_DELAY) -> None:
        self.min_delay = max(float(min_delay), MIN_DELAY)
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            remaining = self._last_request_at + self.min_delay - now
            if remaining > 0:
                time.sleep(remaining)
                now = time.monotonic()
            self._last_request_at = now


class FetchError(RuntimeError):
    """Raised when a page could not be fetched after all retries."""


@dataclass
class FetchResult:
    url: str
    status_code: int
    text: str
    elapsed_s: float

    @property
    def soup(self) -> BeautifulSoup:
        return make_soup(self.text)


def make_soup(html: str) -> BeautifulSoup:
    """Parse *html* with lxml when available, else the stdlib parser."""
    try:  # pragma: no cover - depends on the optional lxml dependency
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")



class PoliteSession:
    """A ``requests.Session`` wrapper that is polite by construction."""

    RETRY_STATUSES = (429, 500, 502, 503, 504)
    #: backoff schedule in seconds, indexed by retry attempt
    BACKOFF = (5.0, 15.0, 45.0)

    def __init__(
        self,
        delay: float = DEFAULT_DELAY,
        timeout: tuple[float, float] = (5.0, 20.0),
        max_retries: int = 3,
        limiter: Optional[RateLimiter] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.delay = max(float(delay), MIN_DELAY)
        self.timeout = timeout
        self.max_retries = max_retries
        self.limiter = limiter or RateLimiter(self.delay)
        self.log = log or logger
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.request_count = 0

    # -- public API ------------------------------------------------------- #

    def build_url(self, url: str, params: Optional[dict] = None) -> str:
        if params:
            query = urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"
        return url

    def get(
        self,
        url: str,
        params: Optional[dict] = None,
        on_retry: Optional[Callable[[int, float, str], None]] = None,
    ) -> FetchResult:
        """Fetch *url* politely, retrying on throttling / transient errors."""
        full_url = assert_url_allowed(self.build_url(url, params))
        last_error: Optional[str] = None
        wait_for = 0.0

        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            started = time.monotonic()
            try:
                response = self.session.get(full_url, timeout=self.timeout)
                elapsed = time.monotonic() - started
                self.request_count += 1

                if response.status_code == 200:
                    self.log.info(
                        "HTTP 200 %s (%.2fs, %d bytes)",
                        full_url,
                        elapsed,
                        len(response.content),
                    )
                    return FetchResult(full_url, 200, response.text, elapsed)

                last_error = f"HTTP {response.status_code}"
                if response.status_code not in self.RETRY_STATUSES:
                    self.log.warning(
                        "HTTP %s for %s (not retryable)", response.status_code, full_url
                    )
                    return FetchResult(full_url, response.status_code, response.text, elapsed)

                wait_for = self._retry_delay(attempt, response.headers.get("Retry-After"))
                self.log.warning(
                    "HTTP %s for %s -> backing off %.1fs (attempt %d/%d)",
                    response.status_code,
                    full_url,
                    wait_for,
                    attempt + 1,
                    self.max_retries,
                )
            except requests.exceptions.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                wait_for = self._retry_delay(attempt, None)
                self.log.warning(
                    "Network error for %s (%s) -> retrying in %.1fs (attempt %d/%d)",
                    full_url,
                    last_error,
                    wait_for,
                    attempt + 1,
                    self.max_retries,
                )

            if on_retry:
                on_retry(attempt + 1, wait_for, last_error or "unknown")
            if attempt < self.max_retries:
                time.sleep(wait_for)

        raise FetchError(
            f"Could not fetch {full_url} after {self.max_retries + 1} attempts ({last_error})"
        )

    @classmethod
    def _retry_delay(cls, attempt: int, retry_after: Optional[str]) -> float:
        if retry_after:
            try:
                return max(float(retry_after), 1.0)
            except (TypeError, ValueError):
                pass
        return cls.BACKOFF[min(attempt, len(cls.BACKOFF) - 1)]

    def close(self) -> None:
        self.session.close()


def search_url(make: str, model: str) -> str:
    """Build the (robots-allowed) search URL for a make/model combination."""
    url = SITE_ROOT + SEARCH_PATH.format(make=make, model=model)
    return assert_url_allowed(url)


# --------------------------------------------------------------------------- #
# Multi-model targets (Feature 2)
# --------------------------------------------------------------------------- #

TARGET_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _clean_label(value: object) -> Optional[str]:
    """Return *value* as stripped text, or ``None`` for blanks/NaN/NA."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):  # arrays / exotic scalars -> treat as text
        pass
    text = str(value).strip()
    return text or None


def make_model_label(make: object, model: object) -> Optional[str]:
    """Identify one model inside a multi-model job.

    ``("honda", "civic")`` -> ``"Honda Civic"``. Either half may be missing, in
    which case the label contains what is known; both missing -> ``None``.
    """
    parts = [pretty_name(text) for text in (_clean_label(make), _clean_label(model)) if text]
    return " ".join(parts) or None


def normalize_slug(value: object, kind: str = "make") -> str:
    """Normalise a make/model input (``"Land Cruiser"`` -> ``"land-cruiser"``)."""
    text = str(value if value is not None else "").strip().lower().replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    if not text:
        raise ValueError(f"'{kind}' is required for every target.")
    if not TARGET_SLUG_RE.match(text):
        raise ValueError(
            f"'{kind}' may only contain letters, digits and hyphens (got '{value}')."
        )
    return text


def normalize_targets(targets: Optional[Iterable[object]]) -> list[tuple[str, str]]:
    """Validate a list of ``(make, model)`` targets and drop duplicates.

    Each item may be a 2-item sequence/iterable or a ``{"make": ..., "model":
    ...}`` mapping. Slugs are lower-cased, spaces/underscores become hyphens and
    repeats are removed while preserving order, so a model is never scraped twice
    in one job. Raises :class:`ValueError` for an empty or malformed list (the HTTP
    layer turns that into a 400, the CLI into a usage error).
    """
    if targets is None:
        raise ValueError("At least one make/model target is required.")
    if isinstance(targets, dict):
        targets = [targets]
    elif isinstance(targets, (str, bytes)):
        raise ValueError("Targets must be (make, model) pairs, not a single string.")

    pairs: list[tuple[str, str]] = []
    for item in targets:
        if isinstance(item, dict):
            make, model = item.get("make"), item.get("model")
        elif isinstance(item, (str, bytes)):
            raise ValueError("Each target must be a (make, model) pair.")
        else:
            try:
                make, model = item
            except (TypeError, ValueError):
                raise ValueError("Each target must be a (make, model) pair.") from None
        pair = (normalize_slug(make, "make"), normalize_slug(model, "model"))
        if pair not in pairs:
            pairs.append(pair)

    if not pairs:
        raise ValueError("At least one make/model target is required.")
    if len(pairs) > MAX_TARGETS:
        raise ValueError(f"At most {MAX_TARGETS} make/model targets can be scraped at once.")
    return pairs


def comparison_label(targets: Optional[Iterable[object]]) -> str:
    """``[("honda", "civic"), ("toyota", "corolla")]`` -> ``"Honda Civic vs Toyota Corolla"``."""
    labels: list[str] = []
    for item in targets or []:
        if isinstance(item, dict):
            label = _clean_label(item.get("make_model")) or make_model_label(
                item.get("make"), item.get("model")
            )
        elif isinstance(item, (str, bytes)):
            label = _clean_label(item)
        else:
            try:
                make, model = item
            except (TypeError, ValueError):
                label = _clean_label(item)
            else:
                label = make_model_label(make, model)
        if label and label not in labels:
            labels.append(label)
    return " vs ".join(labels)


# --------------------------------------------------------------------------- #
# Field parsers
# --------------------------------------------------------------------------- #

NOT_FOR_SALE_TEXT = ("price on call", "call for price", "on call", "sold", "n/a")

FUEL_TYPES = ("petrol", "diesel", "hybrid", "cng", "lpg", "electric")
TRANSMISSION_TYPES = ("automatic", "manual", "cvt", "amt", "dct")

_MULTIPLIERS = (
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:crores?|cr)\b"), 10_000_000),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:lacs?|lakhs?|lakh|lac)\b"), 100_000),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:arabs?)\b"), 1_000_000_000),
    (re.compile(r"(\d+(?:\.\d+)?)\s*k\b"), 1_000),
)

YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def parse_price(price_str: object) -> Optional[float]:
    """Convert a PakWheels price string into a PKR float.

    ``"PKR 12.6 lacs" -> 1260000.0``, ``"1.25 Crore" -> 12500000.0``,
    ``"PKR 850,000" -> 850000.0``, ``"Price on Call" -> None``.
    """
    if price_str is None:
        return None
    if isinstance(price_str, (int, float)) and not isinstance(price_str, bool):
        return float(price_str) or None

    text = str(price_str).strip()
    if not text:
        return None

    lowered = text.lower()
    if any(flag in lowered for flag in NOT_FOR_SALE_TEXT):
        return None

    compact = re.sub(r"[\s,]", "", lowered)  # "pkr12.6lacs"

    for pattern, multiplier in _MULTIPLIERS:
        match = pattern.search(compact)
        if match:
            try:
                value = float(match.group(1)) * multiplier
            except ValueError:  # pragma: no cover - defensive
                continue
            return value or None

    numbers = re.findall(r"\d+(?:\.\d+)?", compact)
    if not numbers:
        return None
    try:
        return float(numbers[0]) or None
    except ValueError:  # pragma: no cover - defensive
        return None


def parse_lacs(price_pkr: Optional[float]) -> Optional[float]:
    """PKR -> lacs (the unit used on the chart's Y axis)."""
    if price_pkr is None:
        return None
    return round(price_pkr / 100_000.0, 3)


def parse_mileage(text: object) -> Optional[int]:
    """``"210,000 km" -> 210000``."""
    if not text:
        return None
    match = re.search(r"([\d,\.]+)\s*(?:km|kms)\b", str(text), re.I)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else None


def parse_engine_cc(text: object) -> Optional[int]:
    """``"1600 cc" -> 1600``."""
    if not text:
        return None
    match = re.search(r"([\d,]+)\s*cc\b", str(text), re.I)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else None


def parse_year(text: object) -> Optional[int]:
    """Extract the 4-digit model year from *text*."""
    if text is None:
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        value = int(text)
        return value if 1950 <= value <= 2049 else None
    match = YEAR_RE.search(str(text))
    return int(match.group(1)) if match else None


def parse_fuel_type(text: object) -> Optional[str]:
    if not text:
        return None
    lowered = str(text).lower()
    for fuel in FUEL_TYPES:
        if fuel in lowered:
            return {"cng": "CNG", "lpg": "LPG"}.get(fuel, fuel.title())
    return None


def parse_transmission(text: object) -> Optional[str]:
    if not text:
        return None
    lowered = str(text).lower()
    for transmission in TRANSMISSION_TYPES:
        if re.search(rf"\b{transmission}\b", lowered):
            return "Automatic" if transmission in ("automatic", "cvt") else transmission.upper()
    return None


def clean_text(text: object) -> Optional[str]:
    if text is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    return cleaned or None

VARIANT_NOISE = {"automatic", "auto", "manual", "cvt", "amt", "dct", "m/t", "a/t"}


def parse_variant(
    title: object, make: Optional[str] = None, model: Optional[str] = None
) -> Optional[str]:
    """Derive the trim from a listing title.

    ``"Honda Civic 2019 VTi Oriel Automatic 1.6 for Sale"`` ->
    ``"VTi Oriel Automatic 1.6"``.
    """
    if not title:
        return None

    text = re.sub(r"\s+", " ", str(title)).strip()
    text = re.sub(r"\s*for\s+sale.*$", "", text, flags=re.I)
    parts = text.split()
    lowered = [re.sub(r"[^\w/]", "", part.lower()) for part in parts]

    index = 0

    def consume(name: Optional[str]) -> None:
        nonlocal index
        if not name:
            return
        tokens = [tok for tok in re.split(r"[\s\-]+", str(name).lower()) if tok]
        if tokens and lowered[index:index + len(tokens)] == tokens:
            index += len(tokens)

    consume(make)
    consume(model)

    rest = parts[index:]
    if rest and YEAR_RE.fullmatch(rest[0]):
        rest = rest[1:]

    variant = re.sub(r"\s+", " ", " ".join(rest)).strip(" -,")
    return variant or None


def variant_family(variant: Optional[str]) -> Optional[str]:
    """Collapse a trim to its family, ``"VTi Oriel Automatic 1.6" -> "VTi Oriel"``."""
    if not variant:
        return None
    kept = []
    for token in str(variant).split():
        lowered = re.sub(r"[^\w.]", "", token.lower())
        if lowered in VARIANT_NOISE:
            continue
        if re.fullmatch(r"\d+(\.\d+)?", lowered):  # engine size, e.g. 1.6 / 1300
            continue
        if re.fullmatch(r"\d+(\.\d+)?(cc|l|litre|liter)", lowered):
            continue
        kept.append(token)
    family = " ".join(kept[:3]).strip()
    return family or None


# --------------------------------------------------------------------------- #
# Search-result parsing
# --------------------------------------------------------------------------- #

CARD_SELECTOR = "li.classified-listing"
NAME_SELECTOR = "a.car-name"
PRICE_SELECTOR = "div.price-details"
INFO1_SELECTOR = "ul.search-vehicle-info"
INFO2_SELECTOR = "ul.search-vehicle-info-2"
DATED_SELECTOR = "div.dated"
RIBBON_SELECTOR = "div.featured-ribbon"

RESULTS_RE = re.compile(r"([\d,]+)\s*-\s*([\d,]+)\s*of\s*([\d,]+)\s*Results", re.I)
PAGE_PARAM_RE = re.compile(r"[?&]page=(\d+)")


#: Computed columns added by the Feature 1 FMV engine (see
#: :func:`calculate_fmv_and_deal_scores`). They are part of ``LISTING_COLUMNS``
#: so CSV/JSON exports and the UI preview pick them up without extra plumbing.
FMV_COLUMNS = [
    "expected_price_pkr",
    "residual_pkr",
    "z_score",
    "deal_rating",
    "fmv_method",
]

LISTING_COLUMNS = [
    "listing_id",
    "make",
    "model",
    #: Human label of the target that produced the row ("Honda Civic"); one
    #: distinct value per model in a multi-model job, used to split Plotly traces.
    "make_model",
    "title",
    "variant",
    "variant_family",
    "year",
    "price_pkr",
    "price_lacs",
    "mileage_km",
    "fuel_type",
    "engine_cc",
    "transmission",
    "location",
    "posted_text",
    "is_featured",
    "url",
    *FMV_COLUMNS,
]

#: Columns the web UI may correct by hand once a scrape has finished:
#: ``(key, label, input type)``. ``listing_id``/``url`` are identities, ``make``/
#: ``model`` are search inputs and ``price_lacs`` is derived from ``price_pkr``, so
#: those stay read-only.
EDITABLE_FIELDS = [
    ("title", "Title", "text"),
    ("year", "Model year", "int"),
    ("price_pkr", "Price (PKR)", "int"),
    ("mileage_km", "Mileage (km)", "int"),
    ("engine_cc", "Engine (cc)", "int"),
    ("fuel_type", "Fuel", "text"),
    ("transmission", "Transmission", "text"),
    ("variant", "Variant", "text"),
    ("variant_family", "Variant family", "text"),
    ("location", "City", "text"),
    ("posted_text", "Posted", "text"),
    ("is_featured", "Featured ad", "bool"),
]

EDITABLE_FIELD_TYPES = {key: kind for key, _label, kind in EDITABLE_FIELDS}

#: Inclusive sanity limits for hand-edited numbers (a typo must not poison the export).
EDITABLE_FIELD_LIMITS = {
    "year": (1950, 2049),
    "price_pkr": (1, 9_999_999_999),
    "mileage_km": (0, 5_000_000),
    "engine_cc": (0, 30_000),
}


def extract_listing_id(url: Optional[str]) -> Optional[str]:
    """Ad id is the trailing number of a listing URL (used as the dedupe key)."""
    if not url:
        return None
    match = re.search(r"-(\d{5,})/?$", urlparse(url).path)
    if match:
        return match.group(1)
    match = re.search(r"/(\d{5,})/?$", urlparse(url).path)
    return match.group(1) if match else None


def _list_items(node) -> list[str]:
    if node is None:
        return []
    return [clean_text(li.get_text(" ", strip=True)) or "" for li in node.find_all("li")]


def parse_total_results(soup: BeautifulSoup) -> Optional[int]:
    """``"1 - 25 of 7998 Results"`` -> ``7998``."""
    if soup is None:
        return None
    match = RESULTS_RE.search(soup.get_text(" ", strip=True))
    if not match:
        return None
    return int(match.group(3).replace(",", ""))


def parse_max_page(soup: BeautifulSoup) -> Optional[int]:
    """Highest ``?page=N`` referenced by the pagination widget."""
    if soup is None:
        return None
    pages = []
    for node in soup.select("ul.pagination a"):
        match = PAGE_PARAM_RE.search(node.get("href") or "")
        if match:
            pages.append(int(match.group(1)))
    return max(pages) if pages else None



def parse_listing_card(
    card, make: Optional[str] = None, model: Optional[str] = None
) -> Optional[dict]:
    """Parse one ``li.classified-listing`` card into a flat dict.

    Returns ``None`` when the card has no usable price (e.g. "Price on Call").
    """
    if card is None:
        return None

    anchor = card.select_one(NAME_SELECTOR) or card.select_one("a.ad-detail-path")
    url = anchor.get("href") if anchor else None
    if url:
        url = url.split("#")[0].strip()
        if url.startswith("/"):
            url = SITE_ROOT + url
        if not is_url_allowed(url):  # e.g. featured ?nf=true links -> drop the query
            base = url.split("?")[0]
            url = base if is_url_allowed(base) else None

    title = clean_text(anchor.get_text(" ", strip=True)) if anchor else None

    price_node = card.select_one(PRICE_SELECTOR)
    price_text = clean_text(price_node.get_text(" ", strip=True)) if price_node else None
    price_pkr = parse_price(price_text)
    if price_pkr is None:
        return None

    info1 = _list_items(card.select_one(INFO1_SELECTOR))
    info2 = _list_items(card.select_one(INFO2_SELECTOR))
    specs = " | ".join(info2 + info1 + [title or ""])

    year = parse_year(info2[0]) if info2 else None
    if year is None:
        year = parse_year(title)

    mileage = None
    for item in info2:
        mileage = parse_mileage(item)
        if mileage is not None:
            break

    location = None
    for item in info1 + info2:
        if (
            parse_year(item) is None
            and parse_mileage(item) is None
            and parse_engine_cc(item) is None
            and parse_fuel_type(item) is None
            and parse_transmission(item) is None
        ):
            location = item
            break

    variant = parse_variant(title, make=make, model=model)
    dated_node = card.select_one(DATED_SELECTOR)

    return {
        "listing_id": extract_listing_id(url),
        "make": make,
        "model": model,
        "make_model": make_model_label(make, model),
        "title": title,
        "variant": variant,
        "variant_family": variant_family(variant),
        "year": year,
        "price_pkr": price_pkr,
        "price_lacs": parse_lacs(price_pkr),
        "mileage_km": mileage,
        "fuel_type": parse_fuel_type(" ".join(info2)) or parse_fuel_type(title),
        "engine_cc": parse_engine_cc(specs),
        "transmission": parse_transmission(" ".join(info2)) or parse_transmission(title),
        "location": location,
        "posted_text": clean_text(dated_node.get_text(" ", strip=True)) if dated_node else None,
        "is_featured": card.select_one(RIBBON_SELECTOR) is not None,
        "url": url,
    }


def dedupe_listings(rows: Iterable[dict]) -> list[dict]:
    """Drop duplicates by ``listing_id`` (featured ads repeat organic results)."""
    seen: set[str] = set()
    unique: list[dict] = []
    for row in rows:
        key = str(row.get("listing_id") or row.get("url") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(row)
    return unique


def parse_listing_cards(
    soup: BeautifulSoup, make: Optional[str] = None, model: Optional[str] = None
) -> list[dict]:
    """Parse and de-duplicate every listing card on a search results page."""
    rows = []
    for card in soup.select(CARD_SELECTOR):
        row = parse_listing_card(card, make=make, model=model)
        if row:
            rows.append(row)
    return dedupe_listings(rows)


def page_size(rows: Sequence[dict]) -> int:
    """Listings per page (25 organic on PakWheels; measured, not assumed)."""
    return len(rows) or RESULTS_PER_PAGE_HINT


# --------------------------------------------------------------------------- #
# Make / model catalogue (one request to the public /used-cars/ page)
# --------------------------------------------------------------------------- #

BRAND_BLOCK_ID = "browse-by-brand_used-cars"
CATALOGUE_LINK_RE = re.compile(r"^/used-cars/([a-z0-9\-]+)/(\d+)/?$")
BIKE_LINK_RE = re.compile(r"^/used-bikes/")

#: Fallback catalogue used when the site cannot be reached (kept deliberately small).
FALLBACK_CATALOGUE: dict[str, list[str]] = {
    "honda": ["civic", "city", "accord", "br-v", "vezel", "hr-v"],
    "toyota": ["corolla", "yaris", "hilux", "fortuner", "prado", "vitz", "aqua", "land-cruiser"],
    "suzuki": ["alto", "cultus", "wagon-r", "swift", "every", "bolan", "ravi", "jimny"],
    "kia": ["sportage", "picanto", "stonic", "carnival", "sorento"],
    "hyundai": ["tucson", "elantra", "santro", "sonata", "ioniq", "porter"],
    "daihatsu": ["mira", "cuore", "terios", "move", "hijet"],
    "nissan": ["dayz", "juke", "x-trail", "note", "sunny"],
    "mg": ["hs", "zs", "5", "rx8", "gt"],
    "haval": ["h6", "jolion", "h9"],
    "changan": ["alsvin", "karvaan", "oshan-x7", "cx70"],
    "mercedes-benz": ["c-class", "e-class", "s-class", "glc"],
    "mitsubishi": ["pajero", "lancer", "ek-wagon", "outlander"],
    "audi": ["a3", "a4", "a5", "a6", "q3", "q5"],
    "byd": ["atto-3", "seal", "han"],
    "jaecoo": ["j7"],
}


def pretty_name(slug: str) -> str:
    """``"mercedes-benz" -> "Mercedes Benz"``, ``"land-cruiser" -> "Land Cruiser"``."""
    return " ".join(part.capitalize() for part in str(slug).replace("_", "-").split("-") if part)


def parse_catalogue(html_or_soup: object) -> dict:
    """Group the make/model links of the brand block into a nested structure.

    PakWheels renders the block as ``<make link> <model link> <model link> ...
    <make link> ...`` where a make link looks like ``/used-cars/honda/14`` and a
    model link like ``/used-cars/honda-civic/642`` (the model slug is prefixed
    with the make slug). Returns::

        {"makes": [{"name": "Honda", "slug": "honda",
                    "models": [{"name": "Civic", "slug": "civic",
                                "full_slug": "honda-civic",
                                "path": "/used-cars/honda-civic/642"}, ...]}, ...]}
    """
    if html_or_soup is None:
        return {"makes": []}

    soup = html_or_soup if isinstance(html_or_soup, BeautifulSoup) else make_soup(str(html_or_soup))
    root = soup.find(id=BRAND_BLOCK_ID) or soup

    makes: list[dict] = []
    current: Optional[dict] = None
    current_make_slug = ""

    for anchor in root.find_all("a", href=True):
        href = (anchor.get("href") or "").split("?")[0]
        if BIKE_LINK_RE.match(href):
            continue
        match = CATALOGUE_LINK_RE.match(href)
        if not match:
            continue

        slug = match.group(1)
        if current is None or not slug.startswith(current_make_slug + "-"):
            if slug == current_make_slug:
                continue  # repeated brand link (the carousel has several slides)
            current_make_slug = slug
            current = next((make for make in makes if make["slug"] == slug), None)
            if current is None:
                current = {"name": pretty_name(slug), "slug": slug, "models": []}
                makes.append(current)
            continue

        model_slug = slug[len(current_make_slug) + 1:]
        if not model_slug or any(model["slug"] == model_slug for model in current["models"]):
            continue
        current["models"].append(
            {
                "name": pretty_name(model_slug),
                "slug": model_slug,
                "full_slug": slug,
                "path": href,
            }
        )

    makes = [make for make in makes if make["models"]]
    makes.sort(key=lambda make: make["name"])
    for make in makes:
        make["models"].sort(key=lambda model: model["name"])
    return {"makes": makes}


def load_config(path: Path = CONFIG_FILE) -> dict:
    """Read ``config.json`` (returns defaults when missing/corrupt)."""
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            logger.warning("config.json could not be read; using defaults")
    return {"default_delay_seconds": DEFAULT_DELAY, "default_pages": DEFAULT_PAGES,
            "max_pages_cap": MAX_PAGES_CAP, "catalogue_ttl_days": CATALOGUE_TTL_DAYS,
            "catalogue": {"built_at": None, "makes": []}}


def save_config(config: dict, path: Path = CONFIG_FILE) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
    except OSError:  # pragma: no cover - defensive
        logger.warning("Could not write %s", path)


def _catalogue_is_fresh(catalogue: dict, ttl_days: int) -> bool:
    built_at = catalogue.get("built_at")
    if not built_at or not catalogue.get("makes"):
        return False
    try:
        built = datetime.fromisoformat(built_at)
    except ValueError:
        return False
    if built.tzinfo is None:
        built = built.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - built).total_seconds() / 86400.0
    return age_days <= ttl_days


def fallback_catalogue() -> dict:
    """Offline catalogue (used only when the live page cannot be fetched)."""
    makes = [
        {"name": pretty_name(slug), "slug": slug,
         "models": [{"name": pretty_name(model), "slug": model} for model in models]}
        for slug, models in FALLBACK_CATALOGUE.items()
    ]
    makes.sort(key=lambda make: make["name"])
    return {"makes": makes}


def fetch_catalogue(
    session: Optional[PoliteSession] = None,
    refresh: bool = False,
    config_path: Path = CONFIG_FILE,
) -> dict:
    """Return the make/model catalogue, using the cached copy when it is fresh.

    Costs exactly one polite request to ``/used-cars/`` when refreshing.
    """
    config = load_config(config_path)
    cached = config.get("catalogue") or {}
    ttl_days = int(config.get("catalogue_ttl_days", CATALOGUE_TTL_DAYS))

    if not refresh and _catalogue_is_fresh(cached, ttl_days):
        logger.info("Using cached catalogue (%d makes)", len(cached.get("makes", [])))
        return cached

    owns_session = session is None
    session = session or PoliteSession()
    try:
        result = session.get(SITE_ROOT + CATALOGUE_PATH)
        catalogue = parse_catalogue(result.soup)
        if not catalogue["makes"]:
            logger.warning("Catalogue parse returned no makes; falling back to bundled list")
            catalogue = fallback_catalogue()
            catalogue["source"] = "fallback"
        else:
            catalogue["source"] = "live"
            logger.info(
                "Catalogue refreshed: %d makes / %d models",
                len(catalogue["makes"]),
                sum(len(make["models"]) for make in catalogue["makes"]),
            )
    except (FetchError, RobotsDisallowed) as exc:
        logger.warning("Catalogue fetch failed (%s); using bundled catalogue", exc)
        catalogue = fallback_catalogue()
        catalogue["source"] = "fallback"
    finally:
        if owns_session:
            session.close()

    catalogue["built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    config["catalogue"] = catalogue
    save_config(config, config_path)
    return catalogue


# --------------------------------------------------------------------------- #
# Estimation
# --------------------------------------------------------------------------- #


def human_duration(seconds: Optional[float]) -> str:
    """``95 -> "1 min 35 s"``."""
    if seconds is None:
        return "unknown"
    seconds = int(round(float(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {secs} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


@dataclass
class Estimate:
    """Everything the UI needs to show an accurate ETA before scraping."""

    make: str
    model: str
    url: str
    delay: float
    latency_s: float = 0.0
    total_results: Optional[int] = None
    page_size: int = RESULTS_PER_PAGE_HINT
    available_pages: Optional[int] = None
    pages_to_scrape: int = 0
    samples: list[dict] = field(default_factory=list)
    measured: bool = True
    notes: str = ""

    @property
    def eta_seconds(self) -> float:
        return estimate_eta(self.pages_to_scrape, self.delay, self.latency_s)

    @property
    def eta_low_s(self) -> float:
        return round(self.eta_seconds * 0.8, 1)

    @property
    def eta_high_s(self) -> float:
        return round(self.eta_seconds * 1.5, 1)

    @property
    def eta_human(self) -> str:
        return human_duration(self.eta_seconds)

    @property
    def eta_range_human(self) -> str:
        return f"{human_duration(self.eta_low_s)} - {human_duration(self.eta_high_s)}"

    @property
    def seconds_per_page(self) -> float:
        return round(max(self.delay, MIN_DELAY) + max(self.latency_s, 0.0) + PAGE_OVERHEAD_S, 2)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload.update(
            {
                "eta_seconds": round(self.eta_seconds, 1),
                "eta_low_s": self.eta_low_s,
                "eta_high_s": self.eta_high_s,
                "eta_human": self.eta_human,
                "eta_range_human": self.eta_range_human,
                "seconds_per_page": self.seconds_per_page,
                "finish_at": datetime.now().astimezone().timestamp() + self.eta_seconds,
            }
        )
        return payload


def estimate_eta(pages: int, delay: float, latency_s: float) -> float:
    """Seconds to scrape *pages* respecting the politeness delay."""
    if pages <= 0:
        return 0.0
    return round(pages * (max(delay, MIN_DELAY) + max(latency_s, 0.0) + PAGE_OVERHEAD_S), 1)


def estimate_scrape(
    session: Optional[PoliteSession] = None,
    make: Optional[Union[str, Iterable[object]]] = "honda",
    model: Optional[str] = "civic",
    max_pages: Optional[int] = DEFAULT_PAGES,
    delay: float = DEFAULT_DELAY,
    sample_size: int = 5,
    first_page: Optional[FetchResult] = None,
    targets: Optional[Iterable[object]] = None,
) -> Estimate:
    """Fetch page 1 once and derive an accurate ETA for the requested scrape.

    Supports single make/model or a list of ``targets`` (Feature 2).
    ``max_pages=None`` means "all available pages". ``first_page`` allows the
    caller to supply an already-fetched page 1 (so the web UI never downloads
    the same page twice).
    """
    if targets is not None or (isinstance(make, (list, tuple, set)) and not isinstance(make, (str, bytes))):
        target_list = targets if targets is not None else make
        pairs = normalize_targets(target_list)
        if len(pairs) == 1:
            return estimate_scrape(
                session=session,
                make=pairs[0][0],
                model=pairs[0][1],
                max_pages=max_pages,
                delay=delay,
                sample_size=sample_size,
                first_page=first_page,
            )
        owns_session = session is None
        session = session or PoliteSession(delay=delay)
        estimates = []
        try:
            for i, (m, md) in enumerate(pairs):
                fp = first_page if i == 0 else None
                estimates.append(
                    estimate_scrape(
                        session=session,
                        make=m,
                        model=md,
                        max_pages=max_pages,
                        delay=delay,
                        sample_size=sample_size,
                        first_page=fp,
                    )
                )
        finally:
            if owns_session:
                session.close()

        total_pages = sum(e.pages_to_scrape for e in estimates)
        total_latencies = [e.latency_s for e in estimates if e.latency_s]
        avg_latency = (sum(total_latencies) / len(total_latencies)) if total_latencies else 0.0
        known_results = [e.total_results for e in estimates if e.total_results is not None]
        known_pages = [e.available_pages for e in estimates if e.available_pages is not None]
        all_samples = []
        for e in estimates:
            all_samples.extend(e.samples)

        combined = Estimate(
            make="multi",
            model="comparison",
            url=search_url(*pairs[0]),
            delay=max(delay, MIN_DELAY),
            latency_s=round(avg_latency, 2),
            total_results=sum(known_results) if known_results else None,
            available_pages=max(known_pages) if known_pages else None,
            pages_to_scrape=total_pages,
            samples=all_samples[:sample_size],
            measured=all(e.measured for e in estimates),
            notes="; ".join(e.notes for e in estimates if e.notes),
        )
        return combined

    make_str = str(make or "honda")
    model_str = str(model or "civic")
    owns_session = session is None
    session = session or PoliteSession(delay=delay)
    url = search_url(make_str, model_str)
    estimate = Estimate(make=make_str, model=model_str, url=url, delay=max(delay, MIN_DELAY))

    try:
        result = first_page if first_page is not None else session.get(url)
        estimate.latency_s = round(result.elapsed_s, 2)

        if result.status_code != 200:
            estimate.measured = False
            estimate.notes = f"Page 1 returned HTTP {result.status_code}; ETA is a rough guess."
            estimate.pages_to_scrape = max_pages or 1
            return estimate

        soup = result.soup
        rows = parse_listing_cards(soup, make=make, model=model)
        estimate.total_results = parse_total_results(soup)
        estimate.page_size = page_size(rows)
        estimate.samples = rows[:sample_size]

        measured_pages = parse_max_page(soup)
        if estimate.total_results:
            computed = math.ceil(estimate.total_results / estimate.page_size)
            estimate.available_pages = max(measured_pages or 0, computed)
        else:
            estimate.available_pages = measured_pages

        if max_pages is None:
            estimate.pages_to_scrape = estimate.available_pages or 1
            estimate.notes = "All available pages were requested."
        else:
            estimate.pages_to_scrape = max(1, min(int(max_pages), MAX_PAGES_CAP))
            if estimate.available_pages and estimate.pages_to_scrape > estimate.available_pages:
                estimate.pages_to_scrape = estimate.available_pages
                estimate.notes = (
                    f"Only {estimate.available_pages} page(s) exist for this model; "
                    "the request was capped."
                )

        if not rows:
            estimate.notes = "No listings parsed on page 1 - this model may have no ads."
        return estimate
    except (FetchError, RobotsDisallowed) as exc:
        estimate.measured = False
        estimate.notes = f"Could not read page 1 ({exc}); ETA is a rough guess."
        estimate.pages_to_scrape = max_pages or 1
        return estimate
    finally:
        if owns_session:
            session.close()


class JobCancelled(RuntimeError):
    """Raised inside the scrape loop when the caller asks for a stop."""


ProgressCallback = Callable[[dict], None]


def scrape_pakwheels_model(
    make: Optional[Union[str, Iterable[object]]] = None,
    model: Optional[str] = None,
    pages: Optional[int] = DEFAULT_PAGES,
    delay: float = DEFAULT_DELAY,
    session: Optional[PoliteSession] = None,
    progress_cb: Optional[ProgressCallback] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    first_page: Optional[FetchResult] = None,
    pages_available: Optional[int] = None,
    targets: Optional[Iterable[object]] = None,
) -> pd.DataFrame:
    """Scrape ``pages`` of results for one make/model (or a list of targets) and return a DataFrame.

    * Supports single make/model or a list of ``targets`` (Feature 2).
    * If ``targets`` is supplied or ``make`` is a list/tuple of targets,
      scraping is delegated to :func:`scrape_pakwheels_targets`.
    * ``pages=None`` -> every available page (capped at :data:`MAX_PAGES_CAP`).
    * ``first_page`` lets the caller reuse the HTML already fetched by
      :func:`estimate_scrape`, so page 1 is never requested twice.
    * ``should_stop`` is polled between pages to support cooperative cancel.
    """
    if targets is not None or (isinstance(make, (list, tuple, set)) and not isinstance(make, (str, bytes))):
        target_list = targets if targets is not None else make
        cached_first = None
        if first_page is not None:
            try:
                first_pair = normalize_targets(target_list)[0]
                cached_first = {first_pair: first_page}
            except Exception:
                pass
        return scrape_pakwheels_targets(
            targets=target_list,
            pages=pages,
            delay=delay,
            session=session,
            progress_cb=progress_cb,
            should_stop=should_stop,
            pages_available=pages_available,
            first_pages=cached_first,
        )

    if not make or not model:
        raise ValueError("Both 'make' and 'model' are required unless 'targets' is provided.")

    make_str = str(make)
    model_str = str(model)
    owns_session = session is None
    session = session or PoliteSession(delay=delay)
    delay = max(float(delay), MIN_DELAY)
    url = search_url(make_str, model_str)

    def notify(stage: str, **extra) -> None:
        if progress_cb:
            event = {"stage": stage, "make": make, "model": model}
            event.update(extra)
            try:
                progress_cb(event)
            except Exception:  # pragma: no cover - progress must never break a scrape
                logger.exception("progress callback failed")

    if pages is None:
        total_pages = min(pages_available or MAX_PAGES_CAP, MAX_PAGES_CAP)
    else:
        total_pages = max(1, min(int(pages), MAX_PAGES_CAP))
    if pages_available:
        total_pages = min(total_pages, pages_available)

    started = time.monotonic()
    rows: list[dict] = []
    seen: set[str] = set()
    duplicate_rows = 0
    skipped_no_price = 0
    pages_done = 0
    failures = 0

    notify("start", pages_total=total_pages, url=url)
    logger.info("Scraping %s %s: %d page(s), delay %.1fs", make, model, total_pages, delay)

    try:
        for page in range(1, total_pages + 1):
            if should_stop and should_stop():
                raise JobCancelled("Cancelled by user")

            try:
                if page == 1 and first_page is not None and first_page.status_code == 200:
                    result = first_page
                else:
                    result = session.get(
                        url,
                        params=None if page == 1 else {"page": page},
                        on_retry=lambda n, wait, err: notify(
                            "retry",
                            page=page,
                            pages_total=total_pages,
                            attempt=n,
                            wait_s=wait,
                            message=err,
                        ),
                    )
            except FetchError as exc:
                failures += 1
                notify("error", page=page, pages_total=total_pages,
                       listings=len(rows), message=str(exc))
                logger.warning("Giving up on page %d: %s", page, exc)
                if failures >= 2:
                    notify("stopped", message="Stopped early: repeated fetch failures.")
                    break
                continue

            if result.status_code != 200:
                failures += 1
                notify("error", page=page, pages_total=total_pages, listings=len(rows),
                       message=f"HTTP {result.status_code}")
                if failures >= 2:
                    notify("stopped", message="Stopped early: repeated HTTP errors.")
                    break
                continue

            page_rows = parse_listing_cards(result.soup, make=make, model=model)
            cards = result.soup.select(CARD_SELECTOR)
            skipped_no_price += max(0, len(cards) - len(page_rows))

            new_rows = []
            for row in page_rows:
                key = str(row.get("listing_id") or row.get("url") or "")
                if key and key in seen:
                    duplicate_rows += 1
                    continue
                if key:
                    seen.add(key)
                new_rows.append(row)
            rows.extend(new_rows)
            pages_done = page

            elapsed = time.monotonic() - started
            notify(
                "page",
                page=page,
                pages_total=total_pages,
                page_listings=len(new_rows),
                listings=len(rows),
                elapsed_s=round(elapsed, 1),
                eta_remaining_s=estimate_eta(total_pages - page, delay, result.elapsed_s),
                url=result.url,
            )
            logger.info(
                "Page %d/%d parsed: %d new listings (%d total)",
                page, total_pages, len(new_rows), len(rows),
            )

            if not page_rows:
                notify("stopped", message=f"No listings on page {page}; stopping.")
                break
    finally:
        if owns_session:
            session.close()

    frame = pd.DataFrame(rows, columns=LISTING_COLUMNS)
    label = make_model_label(make, model)
    if "make_model" in frame.columns and label:
        frame["make_model"] = frame["make_model"].fillna(label)
    notify(
        "done",
        listings=len(frame),
        pages_done=pages_done,
        pages_total=total_pages,
        duplicates_dropped=duplicate_rows,
        skipped_no_price=skipped_no_price,
        elapsed_s=round(time.monotonic() - started, 1),
    )
    return frame


def scrape_pakwheels_targets(
    targets: Iterable[object],
    pages: Optional[int] = DEFAULT_PAGES,
    delay: float = DEFAULT_DELAY,
    session: Optional[PoliteSession] = None,
    progress_cb: Optional[ProgressCallback] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    pages_available: Optional[int] = None,
    first_pages: Optional[dict] = None,
) -> pd.DataFrame:
    """Scrape several ``(make, model)`` targets in one job (Feature 2).

    Every row is stamped with ``make_model`` (``"Honda Civic"``) so the payload
    can render one coloured Plotly trace per model and exports can state which
    models a run contains. A single target is delegated to
    :func:`scrape_pakwheels_model` (identical progress events); several targets
    share one polite session and are scraped in the order given.

    ``first_pages`` maps a ``(make, model)`` pair to the page-1
    :class:`FetchResult` already fetched by :func:`estimate_scrape`, so page 1 is
    never requested twice. ``pages_available`` applies to every target without a
    cached first page; multi-model callers normally leave it ``None``.

    Raises :class:`ValueError` (via :func:`normalize_targets`) for an empty,
    oversized or malformed target list.
    """
    pairs = normalize_targets(targets)
    first_pages = first_pages or {}

    if len(pairs) == 1:
        make, model = pairs[0]
        frame = scrape_pakwheels_model(
            make=make,
            model=model,
            pages=pages,
            delay=delay,
            session=session,
            progress_cb=progress_cb,
            should_stop=should_stop,
            first_page=first_pages.get((make, model)),
            pages_available=pages_available,
        )
        frame = frame.copy()
        frame["make_model"] = make_model_label(make, model)
        return frame

    owns_session = session is None
    session = session or PoliteSession(delay=delay)
    started = time.monotonic()
    frames: list[pd.DataFrame] = []
    per_target: list[dict] = []
    totals = {
        "pages_done": 0,
        "pages_total": 0,
        "duplicates_dropped": 0,
        "skipped_no_price": 0,
    }

    try:
        for index, (make, model) in enumerate(pairs, start=1):
            label = make_model_label(make, model)
            target_stats = {
                "make": make,
                "model": model,
                "make_model": label,
                "pages_done": 0,
                "pages_total": 0,
                "duplicates_dropped": 0,
                "skipped_no_price": 0,
                "listings": 0,
            }

            def forward(event: dict, index=index, target_stats=target_stats) -> None:
                """Tag events with the target and aggregate the per-target totals."""
                if event.get("stage") == "done":
                    for key in ("pages_done", "pages_total", "duplicates_dropped",
                                "skipped_no_price", "listings"):
                        target_stats[key] = int(event.get(key) or 0)
                    event = dict(
                        event,
                        stage="target_done",
                        target_index=index,
                        targets_total=len(pairs),
                        message=f"{target_stats['make_model']}: "
                                f"{target_stats['listings']} listings",
                    )
                else:
                    event = dict(event, target_index=index, targets_total=len(pairs))
                    if event.get("stage") == "start":
                        event["stage"] = "target_start"
                if progress_cb:
                    try:
                        progress_cb(event)
                    except Exception:  # pragma: no cover - progress must never break a scrape
                        logger.exception("progress callback failed")

            frame = scrape_pakwheels_model(
                make=make,
                model=model,
                pages=pages,
                delay=delay,
                session=session,
                progress_cb=forward,
                should_stop=should_stop,
                first_page=first_pages.get((make, model)),
                pages_available=pages_available,
            )
            frame = frame.copy()
            frame["make_model"] = label
            frames.append(frame)
            target_stats["listings"] = int(len(frame))
            per_target.append(target_stats)
            for key in totals:
                totals[key] += target_stats[key]
            logger.info(
                "Target %d/%d %s done: %d listings", index, len(pairs), label, len(frame)
            )
    finally:
        if owns_session:
            session.close()

    combined = (
        pd.concat(frames, ignore_index=True)
        if frames else pd.DataFrame(columns=LISTING_COLUMNS)
    )
    # A featured ad can surface under two model searches; de-duplicate per model
    # so each trace stays clean without collapsing legitimate rows of other models.
    duplicates = 0
    if len(combined) and "listing_id" in combined.columns:
        identifiers = combined["listing_id"].fillna("").astype(str).str.strip()
        if "url" in combined.columns:
            fallbacks = combined["url"].fillna("").astype(str).str.strip()
        else:
            fallbacks = pd.Series("", index=combined.index, dtype=object)
        identifiers = identifiers.where(identifiers != "", fallbacks)
        keys = combined["make_model"].fillna("").astype(str) + "|" + identifiers
        keep = ~((identifiers.str.len() > 0) & keys.duplicated())
        duplicates = int((~keep).sum())
        if duplicates:
            combined = combined.loc[keep].reset_index(drop=True)
    totals["duplicates_dropped"] += duplicates

    if progress_cb:
        try:
            progress_cb(
                {
                    "stage": "done",
                    "listings": int(len(combined)),
                    "pages_done": totals["pages_done"],
                    "pages_total": totals["pages_total"],
                    "duplicates_dropped": totals["duplicates_dropped"],
                    "skipped_no_price": totals["skipped_no_price"],
                    "elapsed_s": round(time.monotonic() - started, 1),
                    "targets_total": len(pairs),
                    "per_target": per_target,
                }
            )
        except Exception:  # pragma: no cover - progress must never break a scrape
            logger.exception("progress callback failed")
    return combined
 
 
def scrape_pakwheels(
    targets: Optional[Iterable[object]] = None,
    make: Optional[str] = None,
    model: Optional[str] = None,
    pages: Optional[int] = DEFAULT_PAGES,
    delay: float = DEFAULT_DELAY,
    session: Optional[PoliteSession] = None,
    progress_cb: Optional[ProgressCallback] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    pages_available: Optional[int] = None,
    first_page: Optional[FetchResult] = None,
    first_pages: Optional[dict] = None,
) -> pd.DataFrame:
    """Unified entry point to scrape either a single model or a list of targets."""
    if targets is not None or (make is not None and not isinstance(make, str)):
        return scrape_pakwheels_targets(
            targets=targets if targets is not None else make,
            pages=pages,
            delay=delay,
            session=session,
            progress_cb=progress_cb,
            should_stop=should_stop,
            pages_available=pages_available,
            first_pages=first_pages,
        )
    return scrape_pakwheels_model(
        make=make,
        model=model,
        pages=pages,
        delay=delay,
        session=session,
        progress_cb=progress_cb,
        should_stop=should_stop,
        first_page=first_page,
        pages_available=pages_available,
    )



# --------------------------------------------------------------------------- #
# Analysis payload + exports
# --------------------------------------------------------------------------- #


def linear_fit(xs: Sequence[float], ys: Sequence[float]) -> Optional[dict]:
    """Ordinary least-squares line for the price-vs-year trend overlay."""
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys)
             if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    sxx = sum(p[0] ** 2 for p in pairs)
    sxy = sum(p[0] * p[1] for p in pairs)
    syy = sum(p[1] ** 2 for p in pairs)
    denominator = n * sxx - sx * sx
    if denominator == 0:
        return None
    slope = (n * sxy - sx * sy) / denominator
    intercept = (sy - slope * sx) / n
    ss_tot = syy - (sy ** 2) / n
    ss_res = syy - intercept * sy - slope * sxy
    r_squared = 1 - ss_res / ss_tot if ss_tot else None
    xs_sorted = sorted(p[0] for p in pairs)
    return {
        "slope": round(slope, 4),
        "intercept": round(intercept, 4),
        "r_squared": round(r_squared, 4) if r_squared is not None else None,
        "x_min": xs_sorted[0],
        "x_max": xs_sorted[-1],
        "y_at_x_min": round(slope * xs_sorted[0] + intercept, 3),
        "y_at_x_max": round(slope * xs_sorted[-1] + intercept, 3),
        "n": n,
    }


def _median(values: Sequence[float]) -> Optional[float]:
    clean = sorted(float(v) for v in values if v is not None)
    if not clean:
        return None
    middle = len(clean) // 2
    if len(clean) % 2:
        return round(clean[middle], 2)
    return round((clean[middle - 1] + clean[middle]) / 2, 2)


# --------------------------------------------------------------------------- #
# FMV & deal rating (Feature 1)
# --------------------------------------------------------------------------- #

#: Minimum usable listings a variant needs before its own price curve is fitted.
FMV_MIN_POINTS = 5

#: Deal-label z-score boundaries. ``-1.2 <= z < -0.5`` is "Good Deal" and
#: ``-0.5 <= z <= 0.8`` is "Fair Price"; the outer bands are the deal extremes.
GREAT_DEAL_Z = -1.2
GOOD_DEAL_Z = -0.5
OVERPRICED_Z = 0.8

UNKNOWN_DEAL_RATING = "Unknown"

#: Deal labels in display order (payload facets, preview filter and chart legend).
DEAL_RATING_ORDER = ("Great Deal", "Good Deal", "Fair Price", "Overpriced", UNKNOWN_DEAL_RATING)

#: Sane model-year window for curve fitting (guards hand-edited typos).
FMV_YEAR_MIN = 1950
FMV_YEAR_MAX = 2100


def deal_rating_from_z(z: Optional[float]) -> str:
    """Map a price z-score onto the deal label used by the FMV engine."""
    try:
        value = float(z)
    except (TypeError, ValueError):
        return UNKNOWN_DEAL_RATING
    if not math.isfinite(value):
        return UNKNOWN_DEAL_RATING
    if value < GREAT_DEAL_Z:
        return "Great Deal"
    if value < GOOD_DEAL_Z:
        return "Good Deal"
    if value <= OVERPRICED_Z:
        return "Fair Price"
    return "Overpriced"


def _fit_variant_curve(years: Sequence[float], prices: Sequence[float]) -> Optional[dict]:
    """Least-squares quadratic ``price ~ model year`` for a single variant.

    The year axis is centred/scaled before the fit so the normal equations stay
    well conditioned for PKR-sized prices. Returns ``None`` when the sample is
    unusable (too few rows, <3 distinct years, non-finite values or zero price
    variance); callers must then fall back to the model-level median.
    """
    x = np.asarray([float(year) for year in years], dtype=float)
    y = np.asarray([float(price) for price in prices], dtype=float)
    if x.size < FMV_MIN_POINTS or x.size != y.size:
        return None
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        return None
    if np.unique(x).size < 3:
        return None

    center = float(x.mean())
    scale = float(x.std())
    if not math.isfinite(scale) or scale <= 0:
        return None
    normalized = (x - center) / scale
    design = np.column_stack((np.ones_like(normalized), normalized, normalized ** 2))
    coefficients, _residuals, rank, _singular = lstsq(design, y)
    if int(rank) < design.shape[1]:
        return None

    fitted = design @ coefficients
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - float(y.mean())) ** 2))
    if not math.isfinite(ss_res) or ss_tot <= 0:
        return None

    residual_std = float(np.std(y - fitted, ddof=1)) if x.size > 1 else 0.0
    # A (near-)exact fit leaves only floating-point noise; treating that as a
    # real spread would blow tiny residuals up into extreme z-scores.
    price_scale = float(np.mean(np.abs(y)))
    if (
        not math.isfinite(residual_std)
        or residual_std <= max(1.0, price_scale) * 1e-9
    ):
        residual_std = 0.0

    return {
        "center": center,
        "scale": scale,
        "coefficients": tuple(float(value) for value in coefficients),
        "residual_std": residual_std,
        "r_squared": round(1.0 - ss_res / ss_tot, 4),
        "rmse": round(math.sqrt(ss_res / x.size), 2),
        "n": int(x.size),
    }


def _predict_variant_curve(model: dict, year: float) -> float:
    """Evaluate a fitted quadratic at ``year`` (same centring as the fit)."""
    normalized = (float(year) - model["center"]) / model["scale"]
    intercept, linear, quadratic = model["coefficients"]
    return intercept + linear * normalized + quadratic * normalized ** 2


def _text_key_column(frame: pd.DataFrame, name: str) -> pd.Series:
    """Return a stripped string column, or blanks when ``name`` is absent."""
    if name not in frame.columns:
        return pd.Series("", index=frame.index, dtype="object")
    values = frame[name]
    return values.where(values.notna(), "").astype(str).str.strip()


def calculate_fmv_and_deal_scores(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Attach fair-market-value estimates and deal labels to a listings frame.

    For every variant with at least :data:`FMV_MIN_POINTS` usable rows (finite
    positive price and a sane model year), a quadratic ``price ~ model year``
    curve is fitted with :func:`scipy.linalg.lstsq`; the model year is centred
    and scaled first so PKR-sized prices stay numerically stable. Each listing
    then gets:

    * ``expected_price_pkr`` -- FMV from its variant curve, or a model-level
      median when the variant sample is too small / cannot be fitted,
    * ``residual_pkr`` -- ``price_pkr - expected_price_pkr``,
    * ``z_score`` -- residual divided by the variant's residual standard
      deviation (model price standard deviation for fallback rows),
    * ``deal_rating`` -- ``Great Deal`` / ``Good Deal`` / ``Fair Price`` /
      ``Overpriced``, or ``Unknown`` when no estimate could be produced,
    * ``fmv_method`` -- provenance: ``variant_quadratic``,
      ``model_year_median``, ``model_median`` or ``unavailable``.

    Graceful fallbacks, in order: median of the same make/model/model-year
    (>= 2 listings), then the median of the whole make/model. Rows without a
    usable price keep ``NaN`` estimates and the ``Unknown`` label. The input
    frame is never mutated -- a copy with the same index and row order is
    returned, ready for the single ``refresh_job_outputs()`` rebuild path.
    """
    frame = df.copy() if df is not None else pd.DataFrame(columns=LISTING_COLUMNS)
    for column in ("expected_price_pkr", "residual_pkr", "z_score"):
        frame[column] = float("nan")
    frame["deal_rating"] = UNKNOWN_DEAL_RATING
    frame["fmv_method"] = "unavailable"

    if frame.empty or "price_pkr" not in frame.columns:
        return frame

    # Positional index keeps the per-row assignments simple and duplicate-safe.
    original_index = frame.index
    frame = frame.reset_index(drop=True)

    prices = pd.to_numeric(frame["price_pkr"], errors="coerce").astype("float64")
    if "year" in frame.columns:
        years = pd.to_numeric(frame["year"], errors="coerce").astype("float64")
    else:
        years = pd.Series(float("nan"), index=frame.index, dtype=float)

    price_ok = prices.notna() & pd.Series(
        np.isfinite(prices.to_numpy(dtype=float)), index=frame.index
    ) & (prices > 0)
    year_ok = years.notna() & (years >= FMV_YEAR_MIN) & (years <= FMV_YEAR_MAX)
    fit_ok = price_ok & year_ok

    make = _text_key_column(frame, "make").str.casefold()
    model = _text_key_column(frame, "model").str.casefold()
    variant = _text_key_column(frame, "variant")
    family = _text_key_column(frame, "variant_family")
    variant = variant.where(variant.str.len() > 0, family)

    model_keys = pd.Series(list(zip(make, model)), index=frame.index, dtype="object")
    variant_keys = pd.Series(
        list(zip(make, model, variant)), index=frame.index, dtype="object"
    )
    work = pd.DataFrame(
        {"price": prices, "year": years, "model_key": model_keys,
         "variant_key": variant_keys},
        index=frame.index,
    )

    # Model-level benchmarks: medians feed the fallback, the standard deviation
    # gives fallback rows a comparable z-score scale.
    valid_prices = work.loc[price_ok]
    model_medians = valid_prices.groupby("model_key")["price"].median().to_dict()
    model_stdevs = valid_prices.groupby("model_key")["price"].std(ddof=1).to_dict()

    year_groups = (
        work.loc[price_ok & year_ok]
        .groupby(["model_key", "year"])["price"]
        .agg(["median", "count"])
    )
    year_medians = {
        (key[0], float(key[1])): float(row["median"])
        for key, row in year_groups.iterrows()
        if int(row["count"]) >= 2
    }

    count = len(frame)
    expected_values = np.full(count, np.nan, dtype=float)
    residual_values = np.full(count, np.nan, dtype=float)
    z_values = np.full(count, np.nan, dtype=float)
    ratings = [UNKNOWN_DEAL_RATING] * count
    methods = ["unavailable"] * count
    fitted_positions: set[int] = set()

    # --- Primary path: per-variant quadratic year -> price curve.
    for _variant_key, group in work.loc[fit_ok].groupby("variant_key", sort=False):
        if len(group) < FMV_MIN_POINTS:
            continue
        curve = _fit_variant_curve(group["year"].tolist(), group["price"].tolist())
        if curve is None:
            continue
        for position, row in group.iterrows():
            expected = _predict_variant_curve(curve, row["year"])
            if not math.isfinite(expected):
                continue
            expected = round(expected, 2)
            residual = float(row["price"]) - expected
            spread = curve["residual_std"]
            z_score = residual / spread if spread > 0 else 0.0
            z_score = round(z_score, 4)
            expected_values[position] = expected
            residual_values[position] = round(residual, 2)
            z_values[position] = z_score
            ratings[position] = deal_rating_from_z(z_score)
            methods[position] = "variant_quadratic"
            fitted_positions.add(position)

    # --- Fallback path: model-year median, then whole-model median.
    for position in np.flatnonzero(price_ok.to_numpy()):
        position = int(position)
        if position in fitted_positions:
            continue
        price = float(work.at[position, "price"])
        model_key = work.at[position, "model_key"]
        model_year = work.at[position, "year"]
        expected = None
        method = "model_median"
        if pd.notna(model_year):
            expected = year_medians.get((model_key, float(model_year)))
            if expected is not None:
                method = "model_year_median"
        if expected is None:
            expected = model_medians.get(model_key)
        if expected is None or not math.isfinite(float(expected)):
            continue
        expected = float(expected)
        expected = round(expected, 2)
        residual = price - expected
        spread = model_stdevs.get(model_key)
        if spread is None or not math.isfinite(float(spread)) or float(spread) <= 0:
            z_score = 0.0
        else:
            z_score = residual / float(spread)
        z_score = round(z_score, 4)
        expected_values[position] = expected
        residual_values[position] = round(residual, 2)
        z_values[position] = z_score
        ratings[position] = deal_rating_from_z(z_score)
        methods[position] = method

    frame["expected_price_pkr"] = expected_values
    frame["residual_pkr"] = residual_values
    frame["z_score"] = z_values
    frame["deal_rating"] = pd.Series(ratings, index=frame.index, dtype="object")
    frame["fmv_method"] = pd.Series(methods, index=frame.index, dtype="object")
    frame.index = original_index
    return frame


def _payload_label(value: object, default: str) -> str:
    """Normalise a categorical cell to a non-empty string."""
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return default
    text = str(value).strip()
    return text or default


def _payload_text(value: object) -> Optional[str]:
    """Normalise an optional text cell to a JSON-safe string or ``None``."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    text = str(value).strip()
    return text or None


def _payload_bool(value: object) -> bool:
    """Coerce a feature flag without letting ``NaN``/``pd.NA`` become ``True``."""
    if value is None or isinstance(value, str) and not value.strip():
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return False
    if isinstance(value, str):
        return value.strip().casefold() in BOOL_TRUE_WORDS
    return bool(value)


def _payload_number(value: object, digits: Optional[int] = None) -> Optional[float]:
    """Coerce a DataFrame cell to a finite JSON number (``None`` when unusable)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits) if digits is not None else number


def build_scatter_payload(
    df: pd.DataFrame,
    make: Optional[str] = None,
    model: Optional[str] = None,
    extra: Optional[dict] = None,
    targets: Optional[Iterable[object]] = None,
) -> dict:
    """Shape a scraped DataFrame into everything the Plotly chart needs.

    Supports multi-model targets (Feature 2) and maintains distinct ``make_model``
    identifiers per point for trace grouping and Plotly styling. Each point carries
    its FMV fields so the chart hover, the preview table and exports stay consistent.
    """
    empty_stats = {
        "count": 0,
        "deal_counts": {label: 0 for label in DEAL_RATING_ORDER},
        "deal_rated_count": 0,
        "fmv_available_count": 0,
        "make_model_counts": {},
    }
    if df is None or len(df) == 0:
        active_targets = targets if targets is not None else (extra.get("targets") if extra else None)
        cmp_meta = comparison_meta(pd.DataFrame(columns=LISTING_COLUMNS), targets=active_targets)
        fallback = make_model_label(make, model)
        empty_models = cmp_meta["make_models"] if cmp_meta["make_models"] else ([fallback] if fallback else [])
        is_cmp = cmp_meta["comparison"] or len(empty_models) > 1
        headline = cmp_meta["make_model"] or fallback or (" vs ".join(empty_models) if empty_models else None)

        meta_dict = {
            "make": make,
            "model": model,
            "make_model": headline,
            "make_models": empty_models,
            "make_model_counts": cmp_meta["make_model_counts"],
            "targets": cmp_meta["targets"],
            "comparison": is_cmp,
        }
        if extra:
            meta_dict.update(extra)
        meta_dict["comparison"] = is_cmp
        meta_dict["make_models"] = empty_models
        meta_dict["make_model"] = headline

        return {
            "points": [], "stats": empty_stats, "facets": {
                "make_model": empty_models,
                "variant_family": [],
                "transmission": [],
                "fuel_type": [],
                "location": [],
                "deal_rating": [],
            },
            "editable_fields": editable_field_specs(),
            "trend": None,
            "meta": meta_dict,
        }

    frame = df.copy()
    score_columns = (
        "expected_price_pkr", "residual_pkr", "z_score", "deal_rating", "fmv_method",
    )
    has_scores = any(
        column in frame.columns and frame[column].notna().any()
        for column in score_columns
    )
    if not has_scores:
        frame = calculate_fmv_and_deal_scores(frame)
    frame = frame[frame["price_pkr"].notna()]
    points = []
    fallback_label = make_model_label(make, model)
    for record in frame.to_dict("records"):
        expected_price_pkr = _payload_number(record.get("expected_price_pkr"), 2)
        points.append(
            {
                "listing_id": record.get("listing_id"),
                "title": _payload_text(record.get("title")),
                "make": _payload_text(record.get("make")),
                "model": _payload_text(record.get("model")),
                "make_model": _payload_text(record.get("make_model"))
                or make_model_label(record.get("make"), record.get("model"))
                or fallback_label,
                "year": int(record["year"]) if pd.notna(record.get("year")) else None,
                "price_pkr": float(record["price_pkr"]),
                "price_lacs": float(record["price_lacs"])
                if pd.notna(record.get("price_lacs")) else None,
                "mileage_km": int(record["mileage_km"])
                if pd.notna(record.get("mileage_km")) else None,
                "engine_cc": int(record["engine_cc"]) if pd.notna(record.get("engine_cc")) else None,
                "fuel_type": _payload_text(record.get("fuel_type")),
                "transmission": _payload_text(record.get("transmission")),
                "variant": _payload_text(record.get("variant")),
                "variant_family": _payload_text(record.get("variant_family")),
                "location": _payload_text(record.get("location")),
                "is_featured": _payload_bool(record.get("is_featured")),
                "posted_text": _payload_text(record.get("posted_text")),
                "url": _payload_text(record.get("url")),
                "expected_price_pkr": expected_price_pkr,
                "expected_price_lacs": round(expected_price_pkr / 100_000.0, 2)
                if expected_price_pkr is not None else None,
                "residual_pkr": _payload_number(record.get("residual_pkr"), 2),
                "z_score": _payload_number(record.get("z_score"), 4),
                "deal_rating": _payload_label(
                    record.get("deal_rating"), UNKNOWN_DEAL_RATING
                ),
                "fmv_method": _payload_label(record.get("fmv_method"), "unavailable"),
            }
        )

    prices = [point["price_pkr"] for point in points]
    years = [point["year"] for point in points if point["year"]]
    mileages = [point["mileage_km"] for point in points if point["mileage_km"]]

    def facet(key: str) -> list[str]:
        values = {point.get(key) for point in points if point.get(key)}
        return sorted(values)

    deal_counts = {label: 0 for label in DEAL_RATING_ORDER}
    for point in points:
        rating = point["deal_rating"]
        deal_counts[rating] = deal_counts.get(rating, 0) + 1

    # Feature 2: keep models in run order for the legend, expose them as a facet
    # for the UI's "Colour by" control, and count listings per model.
    make_models: list[str] = []
    make_model_counts: dict[str, int] = {}
    for point in points:
        label = point.get("make_model")
        if not label:
            continue
        if label not in make_models:
            make_models.append(label)
        make_model_counts[label] = make_model_counts.get(label, 0) + 1

    trend = linear_fit(
        [point["year"] for point in points],
        [point["price_lacs"] for point in points],
    )

    stats = {
        "count": len(points),
        "rows_scraped": int(len(df)),
        "rows_dropped_no_year": int(len(df) - len(frame)),
        "price_min": round(min(prices), 0),
        "price_median": _median(prices),
        "price_max": round(max(prices), 0),
        "price_min_lacs": round(min(prices) / 100_000.0, 2),
        "price_max_lacs": round(max(prices) / 100_000.0, 2),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "mileage_median_km": _median(mileages) if mileages else None,
        "featured_count": sum(1 for point in points if point["is_featured"]),
        "missing_year_count": sum(1 for point in points if not point["year"]),
        "deal_counts": deal_counts,
        "deal_rated_count": sum(
            count for label, count in deal_counts.items()
            if label != UNKNOWN_DEAL_RATING
        ),
        "fmv_available_count": sum(
            1 for point in points if point["fmv_method"] != "unavailable"
        ),
        "make_model_counts": make_model_counts,
    }

    active_targets = targets if targets is not None else (extra.get("targets") if extra else None)
    cmp_meta = comparison_meta(frame, targets=active_targets)
    for mm in cmp_meta["make_models"]:
        if mm not in make_models:
            make_models.append(mm)

    is_comparison = len(make_models) > 1 or cmp_meta["comparison"]
    headline = cmp_meta["make_model"] or (make_model_label(make, model) if make and model else (" vs ".join(make_models) if make_models else None))
    stats["make_model_counts"] = cmp_meta["make_model_counts"] or make_model_counts

    meta_dict = {
        "make": make,
        "model": model,
        "make_model": headline,
        "make_models": make_models,
        "make_model_counts": stats["make_model_counts"],
        "targets": cmp_meta["targets"],
        "comparison": is_comparison,
    }
    if extra:
        meta_dict.update(extra)
    meta_dict["comparison"] = is_comparison
    meta_dict["make_models"] = make_models
    meta_dict["make_model"] = headline
    meta_dict["make_model_counts"] = stats["make_model_counts"]
    meta_dict["targets"] = cmp_meta["targets"]

    return {
        "points": points,
        "stats": stats,
        "facets": {
            "make_model": make_models,
            "variant_family": facet("variant_family"),
            "transmission": facet("transmission"),
            "fuel_type": facet("fuel_type"),
            "location": facet("location"),
            "deal_rating": [
                label for label in DEAL_RATING_ORDER if deal_counts.get(label, 0)
            ],
        },
        "trend": trend,
        "editable_fields": editable_field_specs(),
        "meta": meta_dict,
    }


def comparison_meta(
    df: Optional[pd.DataFrame], targets: Optional[Iterable[object]] = None
) -> dict:
    """Describe a run's make/model coverage for ``meta.json`` (Feature 2).

    Returns ``comparison`` (True when the run holds more than one model),
    ``make_models`` (labels in run order), ``make_model`` (``"Honda Civic vs
    Toyota Corolla"`` headline), ``make_model_counts`` and ``targets`` (one entry
    per requested model with its listing count, including requested models that
    returned no rows).

    ``targets`` is normally the job's requested ``(make, model)`` list; when it is
    omitted the labels come from the frame's ``make_model`` / ``make`` + ``model``
    columns. Single-model runs receive the same keys with ``comparison: False``,
    so every export states which model(s) it contains.
    """
    frame = df if df is not None else pd.DataFrame(columns=LISTING_COLUMNS)

    make_values = frame["make"].tolist() if "make" in frame.columns else [None] * len(frame)
    model_values = frame["model"].tolist() if "model" in frame.columns else [None] * len(frame)
    raw_mm = frame["make_model"].tolist() if "make_model" in frame.columns else [None] * len(frame)
    rows = [
        (_clean_label(mm) or make_model_label(mk, md), mk, md)
        for mm, mk, md in zip(raw_mm, make_values, model_values)
    ]

    counts: dict[str, int] = {}
    labels: list[str] = []
    origin: dict[str, tuple[Optional[str], Optional[str]]] = {}
    for value, make, model in rows:
        label = _clean_label(value) or make_model_label(make, model)
        if not label:
            continue
        key = label.casefold()
        if key not in counts:
            labels.append(label)
            counts[key] = 0
            origin[key] = (_clean_label(make), _clean_label(model))
        counts[key] += 1

    entries: list[dict] = []
    seen: set[str] = set()

    def add_entry(make=None, model=None, label=None) -> None:
        label = _clean_label(label) or make_model_label(make, model)
        if not label:
            return
        key = label.casefold()
        if key in seen:
            return
        seen.add(key)
        if make is None and model is None:
            make, model = origin.get(key, (None, None))
        entries.append(
            {
                "make": _clean_label(make),
                "model": _clean_label(model),
                "make_model": label,
                "listings": counts.get(key, 0),
            }
        )

    if targets is not None:
        try:
            requested = normalize_targets(targets)
        except ValueError:
            requested = []
        for make, model in requested:
            add_entry(make=make, model=model)

    # Rows whose label was not a requested target (e.g. legacy frames) still count.
    for label in labels:
        add_entry(label=label)

    ordered_labels = [entry["make_model"] for entry in entries]
    return {
        "comparison": len(entries) > 1,
        "make_model": " vs ".join(ordered_labels) or None,
        "make_models": ordered_labels,
        "make_model_counts": {
            entry["make_model"]: entry["listings"] for entry in entries
        },
        "targets": entries,
    }


def merge_run_meta(df: pd.DataFrame, meta: Optional[dict]) -> dict:
    """Return *meta* enriched with the comparison keys derived from *df*.

    The derived keys (``comparison``, ``make_model``, ``make_models``,
    ``make_model_counts``, ``targets``) are authoritative: they always describe
    the rows actually exported, even after manual corrections or a partial scrape.
    """
    merged = dict(meta or {})
    merged.update(comparison_meta(df, merged.get("targets")))
    return merged


def new_run_dir(job_id: str) -> Path:
    """Create (and return) ``data/runs/<job_id>/``."""
    path = RUNS_DIR / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_run(df: pd.DataFrame, meta: dict, run_dir: Path) -> dict:
    """Persist listings as CSV + JSON and the run metadata as ``meta.json``.

    Metadata is enriched by :func:`merge_run_meta` first, so every export labels
    the model(s) the run contains (``comparison`` / ``make_models`` / ``targets``).
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = merge_run_meta(df, meta)
    csv_path = run_dir / "listings.csv"
    json_path = run_dir / "listings.json"
    meta_path = run_dir / "meta.json"

    export_df = df.copy() if df is not None else pd.DataFrame(columns=LISTING_COLUMNS)
    if "make_model" not in export_df.columns:
        if "make" in export_df.columns and "model" in export_df.columns:
            export_df["make_model"] = [
                make_model_label(m, md) for m, md in zip(export_df["make"], export_df["model"])
            ]
        else:
            export_df["make_model"] = None
    elif "make" in export_df.columns and "model" in export_df.columns:
        fallback_labels = [
            make_model_label(m, md) for m, md in zip(export_df["make"], export_df["model"])
        ]
        export_df["make_model"] = export_df["make_model"].fillna(
            pd.Series(fallback_labels, index=export_df.index)
        )

    export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    export_df.to_json(json_path, orient="records", indent=2, force_ascii=False)
    with meta_path.open("w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, ensure_ascii=False, default=str)

    logger.info("Saved run to %s (%d rows)", run_dir, len(export_df))
    return {
        "dir": str(run_dir),
        "csv": str(csv_path),
        "json": str(json_path),
        "meta": str(meta_path),
    }


SCATTER_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{title}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body {{ font-family: Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 24px; color: #10233f; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  p.meta {{ color: #5a6b85; margin: 0 0 16px; font-size: 13px; }}
  #chart {{ width: 100%; height: 640px; }}
  .deal-keys {{ display: flex; flex-wrap: wrap; gap: 12px; margin: -8px 0 12px; font-size: 12px; color: #5a6b85; }}
  .deal-key {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; vertical-align: -1px; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="meta">{subtitle}</p>
<div class="deal-keys">
  <span><span class="deal-key" style="background:#1a9850"></span>Great Deal</span>
  <span><span class="deal-key" style="background:#66bd63"></span>Good Deal</span>
  <span><span class="deal-key" style="background:#4c78a8"></span>Fair Price</span>
  <span><span class="deal-key" style="background:#d73027"></span>Overpriced</span>
  <span><span class="deal-key" style="background:#9aa5b1"></span>Unknown</span>
</div>
<div id="chart"></div>
<script>
const PAYLOAD = {payload_json};

const DEAL_COLORS = {{
  "Great Deal": "#1a9850", "Good Deal": "#66bd63", "Fair Price": "#4c78a8",
  "Overpriced": "#d73027", "Unknown": "#9aa5b1"
}};

function dealColor(rating) {{ return DEAL_COLORS[rating] || DEAL_COLORS.Unknown; }}

function fmtValue(value, suffix) {{
  return (value === null || value === undefined) ? "n/a" : value + suffix;
}}

function traceForGroup(key) {{
  const groups = new Map();
  PAYLOAD.points.forEach(p => {{
    const g = p[key] || "Unknown";
    if (!groups.has(g)) groups.set(g, {{ x: [], y: [], text: [], customdata: [], colors: [] }});
    const bucket = groups.get(g);
    bucket.x.push(p.year);
    bucket.y.push(p.price_lacs);
    bucket.text.push(p.title || "");
    bucket.customdata.push([
      fmtValue(p.mileage_km, " km"),
      p.location || "n/a",
      p.transmission || "n/a",
      p.url || "",
      p.price_pkr,
      fmtValue(p.expected_price_lacs, " lacs"),
      fmtValue(p.residual_pkr, " PKR"),
      fmtValue(p.z_score, ""),
      p.deal_rating || "Unknown",
      p.fmv_method || "unavailable"
    ]);
    bucket.colors.push(dealColor(p.deal_rating));
  }});
  return Array.from(groups.entries()).map(([name, bucket]) => {{
    const marker = {{
      size: 10, opacity: 0.8,
      line: {{ width: 0.5, color: "#ffffff" }}
    }};
    if (key !== "make_model") {{
      marker.color = bucket.colors;
    }}
    return ({{
      type: "scatter", mode: "markers", name: name,
      x: bucket.x, y: bucket.y, text: bucket.text, customdata: bucket.customdata,
      hovertemplate: "<b>%{{text}}</b><br>Year: %{{x}}<br>Price: %{{y}} lacs"
        + "<br>Mileage: %{{customdata[0]}}<br>City: %{{customdata[1]}}"
        + "<br>Transmission: %{{customdata[2]}}"
        + "<br>FMV: %{{customdata[5]}} (residual %{{customdata[6]}})"
        + "<br>Deal: %{{customdata[8]}} (z=%{{customdata[7]}}, %{{customdata[9]}})"
        + "<extra>" + name + "</extra>",
      marker: marker
    }});
  }});
}}

const groupKey = (PAYLOAD.meta && PAYLOAD.meta.comparison) ? "make_model" : "variant_family";
const traces = traceForGroup(groupKey);
const trend = PAYLOAD.trend;
if (trend) {{
  traces.push({{
    type: "scatter", mode: "lines", name: "Trend",
    x: [trend.x_min, trend.x_max], y: [trend.y_at_x_min, trend.y_at_x_max],
    line: {{ color: "#e2574c", width: 2, dash: "dash" }},
    hovertemplate: "Trend line<extra></extra>"
  }});
}}

Plotly.newPlot("chart", traces, {{
  title: {{ text: "Price (lacs PKR) vs Model Year", font: {{ size: 15 }} }},
  xaxis: {{ title: "Model year", dtick: 1, tickangle: -45 }},
  yaxis: {{ title: "Price (lacs PKR)" }},
  legend: {{ orientation: "h", y: -0.2 }},
  margin: {{ t: 48, r: 24, b: 90, l: 70 }},
  hovermode: "closest"
}}, {{ responsive: true, displaylogo: false }});
</script>
</body>
</html>
"""


def render_scatter_html(payload: dict, title: str, subtitle: str = "") -> str:
    """Build a standalone interactive scatter page (no Python plotly needed)."""
    meta = payload.get("meta") or {}
    stats = payload.get("stats") or {}
    if not subtitle:
        pieces = [f"{stats.get('count', 0)} listings"]
        if meta.get("make_model"):
            pieces.append(str(meta.get("make_model")))
        elif meta.get("make"):
            pieces.append(f"{meta.get('make')} {meta.get('model') or ''}".strip())
        if meta.get("generated_at"):
            pieces.append(f"generated {meta['generated_at']}")
        if meta.get("source_url"):
            pieces.append(meta["source_url"])
        subtitle = " &middot; ".join(pieces)

    return SCATTER_HTML_TEMPLATE.format(
        title=title,
        subtitle=subtitle,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/"),
    )


def save_scatter_html(payload: dict, path: Path, title: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_scatter_html(payload, title), encoding="utf-8")
    return path


def dataframe_preview(df: pd.DataFrame, limit: int = 10) -> list[dict]:
    """Small, JSON-safe table preview for the UI (never raises on NaN)."""
    if df is None or len(df) == 0:
        return []
    columns = [col for col in LISTING_COLUMNS if col in df.columns]
    subset = df.head(limit)[columns]
    return json.loads(subset.to_json(orient="records", force_ascii=False))


# --------------------------------------------------------------------------- #
# Hand corrections (the web UI fixes mis-parsed values before export)
# --------------------------------------------------------------------------- #

BOOL_TRUE_WORDS = {"1", "true", "yes", "y", "on", "featured"}
BOOL_FALSE_WORDS = {"", "0", "false", "no", "n", "off"}


def editable_field_specs() -> list[dict]:
    """JSON-safe description of the hand-editable columns (consumed by the UI)."""
    specs = []
    for key, label, kind in EDITABLE_FIELDS:
        spec = {"key": key, "label": label, "type": kind}
        low, high = EDITABLE_FIELD_LIMITS.get(key, (None, None))
        if low is not None:
            spec["min"] = low
            spec["max"] = high
            spec["required"] = key == "price_pkr"
        specs.append(spec)
    return specs


def json_scalar(value: object):
    """Turn a DataFrame cell (``NaN`` / numpy scalar / ``None``) into plain JSON."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return bool(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    if isinstance(value, (int, float)):
        return value
    item = getattr(value, "item", None)  # numpy scalars arrive as np.int64 / np.bool_
    if callable(item):
        return item()
    return str(value)  # pragma: no cover - defensive


def coerce_editable_value(field: str, raw: object):
    """Validate and normalise one hand-edited value.

    Raises ``ValueError`` with a message meant for the user when *field* is not
    editable or *raw* cannot be turned into a sane value.
    """
    kind = EDITABLE_FIELD_TYPES.get(field)
    if kind is None:
        editable = ", ".join(sorted(EDITABLE_FIELD_TYPES))
        raise ValueError(f"'{field}' cannot be edited by hand (editable: {editable}).")

    if kind == "text":
        return clean_text(raw)

    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        if raw is None:
            return False
        text = str(raw).strip().lower()
        if text in BOOL_TRUE_WORDS:
            return True
        if text in BOOL_FALSE_WORDS:
            return False
        raise ValueError(f"'{field}' must be true or false.")

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        value = None  # blank means "unknown" and is exported as an empty cell
    else:
        if isinstance(raw, bool):
            raise ValueError(f"'{field}' must be a whole number.")
        try:
            number = float(re.sub(r"[,\s]", "", str(raw)))
        except (TypeError, ValueError):
            raise ValueError(f"'{field}' must be a whole number.")
        if not number.is_integer():
            raise ValueError(f"'{field}' must be a whole number.")
        value = int(number)
        low, high = EDITABLE_FIELD_LIMITS.get(field, (None, None))
        if low is not None and not low <= value <= high:
            raise ValueError(f"'{field}' must be between {low} and {high}.")

    if field == "price_pkr" and value is None:
        raise ValueError("'price_pkr' is required (a listing without a price is dropped).")
    return value


def apply_listing_edit(
    df: pd.DataFrame, listing_id: object, updates: dict
) -> tuple[pd.DataFrame, list[dict]]:
    """Apply hand-corrected values to a single listing.

    Returns ``(edited_copy, changes)`` where *changes* is a JSON-safe list of
    ``{"field", "from", "to"}`` entries for the values that actually moved.
    ``ValueError`` is raised for an empty result set, an unknown listing, a
    read-only/unknown field or an out-of-range value, so the caller can hand the
    message straight back to the UI.
    """
    if df is None or len(df) == 0:
        raise ValueError("There are no listings to correct.")
    if not isinstance(updates, dict) or not updates:
        raise ValueError("'fields' must be a non-empty object of field -> value.")
    if "listing_id" not in df.columns:
        raise ValueError("This result set has no listing ids to match on.")

    key = str(listing_id or "").strip()
    if not key:
        raise ValueError("'listing_id' is required.")
    matches = df.index[df["listing_id"].astype(str) == key]
    if len(matches) == 0:
        raise ValueError(f"Unknown listing id '{key}'.")

    frame = df.copy()
    row = matches[0]
    changes: list[dict] = []

    for field, raw in updates.items():
        value = coerce_editable_value(field, raw)
        if field not in frame.columns:
            frame[field] = None
        frame[field] = frame[field].astype(object)
        previous = json_scalar(frame.at[row, field])
        if previous == value:
            continue
        frame.at[row, field] = value
        changes.append({"field": field, "from": previous, "to": value})

    if not changes:
        return frame, []

    # price_lacs is always derived from price_pkr (single source of truth).
    if any(change["field"] == "price_pkr" for change in changes):
        price = json_scalar(frame.at[row, "price_pkr"])
        if "price_lacs" in frame.columns:
            frame["price_lacs"] = frame["price_lacs"].astype(object)
            frame.at[row, "price_lacs"] = parse_lacs(float(price)) if price else None

    # A corrected trim should refresh its family unless the family was edited too.
    if "variant_family" not in updates and any(
        change["field"] == "variant" for change in changes
    ):
        family = variant_family(json_scalar(frame.at[row, "variant"]))
        if "variant_family" not in frame.columns:
            frame["variant_family"] = None
        frame["variant_family"] = frame["variant_family"].astype(object)
        previous = json_scalar(frame.at[row, "variant_family"])
        if previous != family:
            frame.at[row, "variant_family"] = family
            changes.append({"field": "variant_family", "from": previous, "to": family})

    return frame, changes

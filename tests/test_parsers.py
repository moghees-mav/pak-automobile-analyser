"""Offline unit tests for the PakWheels parser layer.

No network access: every test runs against the HTML fixtures below, which mirror
the live markup verified on www.pakwheels.com.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pw_scraper as pw  # noqa: E402

# --------------------------------------------------------------------------- #
# Fixtures (trimmed copies of the real search-result markup)
# --------------------------------------------------------------------------- #

RESULTS_PAGE = """
<html><body>
<div class="search-page-heading">1 - 25 of 7998 Results</div>
<ul class="classified-listing-list">
  <li class="classified-listing">
    <div class="col-md-3 grid-style"><img src="/img/1.jpg" /></div>
    <div class="col-md-9 grid-style">
      <div class="search-title-row">
        <div class="name-price">
          <div class="search-title">
            <a class="car-name ad-detail-path"
               href="/used-cars/honda-civic-1999-for-sale-in-lahore-12124197">
              <h3>Honda Civic 1999 VTi Oriel Automatic 1.6 for Sale</h3>
            </a>
          </div>
          <div class="price-details generic-dark-grey">PKR 12.6 lacs</div>
        </div>
      </div>
      <div class="row mt0">
        <div class="col-md-12 grid-date">
          <ul class="list-unstyled search-vehicle-info fs13"><li>Lahore</li></ul>
          <ul class="list-unstyled search-vehicle-info-2 fs13">
            <li>1999</li><li>210,000 km</li><li>Petrol</li>
            <li>1600 cc</li><li>Automatic</li>
          </ul>
        </div>
      </div>
      <div class="search-bottom clearfix">
        <div class="pull-left dated">Updated 5 minutes ago</div>
      </div>
    </div>
  </li>
  <li class="classified-listing">
    <div class="col-md-9 grid-style">
      <div class="featured-ribbon">FEATURED</div>
      <div class="search-title">
        <a class="car-name ad-detail-path"
           href="/used-cars/toyota-corolla-2021-altis-grande-for-sale-in-karachi-99887766?nf=true">
          <h3>Toyota Corolla 2021 Altis Grande 1.8 for Sale</h3>
        </a>
      </div>
      <div class="price-details">PKR 65.5 lacs</div>
      <ul class="list-unstyled search-vehicle-info fs13"><li>Karachi</li></ul>
      <ul class="list-unstyled search-vehicle-info-2 fs13">
        <li>2021</li><li>38,000 km</li><li>Petrol</li><li>1800 cc</li><li>Automatic</li>
      </ul>
      <div class="pull-left dated">Updated 1 hour ago</div>
    </div>
  </li>
  <li class="classified-listing">
    <a class="car-name ad-detail-path"
       href="/used-cars/suzuki-alto-2012-vxl-for-sale-in-islamabad-55443322">
      <h3>Suzuki Alto 2012 VXL for Sale</h3>
    </a>
    <div class="price-details">Price on Call</div>
    <ul class="list-unstyled search-vehicle-info-2 fs13">
      <li>2012</li><li>90,000 km</li><li>Petrol</li><li>660 cc</li><li>Manual</li>
    </ul>
  </li>
  <li class="classified-listing">
    <a class="car-name ad-detail-path"
       href="/used-cars/kia-sportage-2018-awd-for-sale-in-lahore-11223344">
      <h3>Kia Sportage 2018 AWD for Sale</h3>
    </a>
    <div class="price-details">PKR 62 lacs</div>
    <ul class="list-unstyled search-vehicle-info fs13"><li>Lahore</li></ul>
  </li>
</ul>
<ul class="pagination">
  <li><a href="/used-cars/search/-/mk_honda/md_civic/?page=2">2</a></li>
  <li><a href="/used-cars/search/-/mk_honda/md_civic/?page=40">Last &raquo;</a></li>
</ul>
</body></html>
"""

CATALOGUE_PAGE = """
<html><body>
<div class="col-md-12">
  <div id="browse-by-brand_used-cars" class="clearfix pos-rel carousel slide lazy-slider">
    <a href="/used-cars/honda/14" class="brand-link">Honda</a>
    <a href="/used-cars/honda-civic/642">Civic</a>
    <a href="/used-cars/honda-city/664">City</a>
    <a href="/used-cars/toyota/33" class="brand-link">Toyota</a>
    <a href="/used-cars/toyota-corolla/688">Corolla</a>
    <a href="/used-cars/toyota-land-cruiser/651">Land Cruiser</a>
    <a href="/used-cars/honda/14">Honda</a>
  </div>
  <div id="browse-by-brand_used-bikes">
    <a href="/used-bikes/honda/cd-70/11">CD 70</a>
  </div>
</div>
</body></html>
"""


class PriceParserTests(unittest.TestCase):
    def test_lacs_and_crore(self):
        self.assertEqual(pw.parse_price("PKR 12.6 lacs"), 1_260_000)
        self.assertEqual(pw.parse_price("45 lacs"), 4_500_000)
        self.assertEqual(pw.parse_price("1.25 Crore"), 12_500_000)
        self.assertEqual(pw.parse_price("PKR 9.5 Lac"), 950_000)

    def test_plain_and_unavailable(self):
        self.assertEqual(pw.parse_price("PKR 850,000"), 850_000)
        self.assertEqual(pw.parse_price("Rs. 1,250,000"), 1_250_000)
        self.assertIsNone(pw.parse_price("Price on Call"))
        self.assertIsNone(pw.parse_price("Call for price"))
        self.assertIsNone(pw.parse_price(""))
        self.assertIsNone(pw.parse_price(None))

    def test_lacs_helper(self):
        self.assertEqual(pw.parse_lacs(1_260_000), 12.6)
        self.assertIsNone(pw.parse_lacs(None))


class FieldParserTests(unittest.TestCase):
    def test_mileage(self):
        self.assertEqual(pw.parse_mileage("210,000 km"), 210_000)
        self.assertEqual(pw.parse_mileage("39,000 km"), 39_000)
        self.assertIsNone(pw.parse_mileage("210000"))

    def test_engine_and_year(self):
        self.assertEqual(pw.parse_engine_cc("1600 cc"), 1600)
        self.assertEqual(pw.parse_engine_cc("660 cc"), 660)
        self.assertIsNone(pw.parse_engine_cc("Automatic"))
        self.assertEqual(pw.parse_year("1999"), 1999)
        self.assertEqual(pw.parse_year(2015), 2015)
        self.assertEqual(pw.parse_year("Honda Civic 2007 for Sale"), 2007)
        self.assertIsNone(pw.parse_year("Automatic"))

    def test_fuel_and_transmission(self):
        self.assertEqual(pw.parse_fuel_type("Petrol"), "Petrol")
        self.assertEqual(pw.parse_fuel_type("Hybrid"), "Hybrid")
        self.assertEqual(pw.parse_fuel_type("CNG"), "CNG")
        self.assertIsNone(pw.parse_fuel_type("Lahore"))
        self.assertEqual(pw.parse_transmission("Automatic"), "Automatic")
        self.assertEqual(pw.parse_transmission("CVT"), "Automatic")
        self.assertEqual(pw.parse_transmission("Manual"), "MANUAL")
        self.assertIsNone(pw.parse_transmission("Lahore"))

    def test_variant(self):
        self.assertEqual(
            pw.parse_variant("Honda Civic 1999 VTi Oriel Automatic 1.6 for Sale", "honda", "civic"),
            "VTi Oriel Automatic 1.6",
        )
        self.assertEqual(pw.variant_family("VTi Oriel Automatic 1.6"), "VTi Oriel")
        self.assertEqual(
            pw.variant_family(pw.parse_variant("Kia Sportage 2018 AWD for Sale", "kia", "sportage")),
            "AWD",
        )
        self.assertIsNone(pw.parse_variant(None))


class ListingCardTests(unittest.TestCase):
    def setUp(self):
        self.soup = pw.make_soup(RESULTS_PAGE)
        self.rows = pw.parse_listing_cards(self.soup, make="honda", model="civic")

    def test_price_on_call_card_is_skipped(self):
        self.assertEqual(len(self.rows), 3)  # fixture has 4 cards, one is "Price on Call"

    def test_first_card_fields(self):
        first = next(row for row in self.rows if row["listing_id"] == "12124197")
        self.assertEqual(first["year"], 1999)
        self.assertEqual(first["price_pkr"], 1_260_000)
        self.assertEqual(first["price_lacs"], 12.6)
        self.assertEqual(first["mileage_km"], 210_000)
        self.assertEqual(first["fuel_type"], "Petrol")
        self.assertEqual(first["engine_cc"], 1600)
        self.assertEqual(first["transmission"], "Automatic")
        self.assertEqual(first["location"], "Lahore")
        self.assertEqual(first["variant"], "VTi Oriel Automatic 1.6")
        self.assertEqual(first["variant_family"], "VTi Oriel")
        self.assertEqual(first["posted_text"], "Updated 5 minutes ago")
        self.assertFalse(first["is_featured"])

    def test_disallowed_query_is_stripped_from_listing_url(self):
        featured = next(row for row in self.rows if row["is_featured"])
        self.assertEqual(featured["listing_id"], "99887766")
        self.assertNotIn("nf=true", featured["url"])
        self.assertTrue(pw.is_url_allowed(featured["url"]))

    def test_card_without_second_info_list_falls_back_to_title_year(self):
        row = next(r for r in self.rows if r["listing_id"] == "11223344")
        self.assertEqual(row["year"], 2018)  # parsed from the title
        self.assertIsNone(row["mileage_km"])
        self.assertIsNone(row["engine_cc"])
        self.assertEqual(row["location"], "Lahore")

    def test_total_results_and_max_page(self):
        self.assertEqual(pw.parse_total_results(self.soup), 7998)
        self.assertEqual(pw.parse_max_page(self.soup), 40)

    def test_extract_listing_id(self):
        self.assertEqual(
            pw.extract_listing_id(
                "https://www.pakwheels.com/used-cars/honda-civic-1999-for-sale-in-lahore-12124197"
            ),
            "12124197",
        )
        self.assertIsNone(pw.extract_listing_id(None))

    def test_dedupe_listings(self):
        rows = [
            {"listing_id": "1", "url": "a"},
            {"listing_id": "1", "url": "b"},
            {"listing_id": "2", "url": "c"},
        ]
        self.assertEqual(len(pw.dedupe_listings(rows)), 2)


class CatalogueTests(unittest.TestCase):
    def test_parse_catalogue(self):
        catalogue = pw.parse_catalogue(CATALOGUE_PAGE)
        self.assertEqual([make["slug"] for make in catalogue["makes"]], ["honda", "toyota"])
        honda = catalogue["makes"][0]
        self.assertEqual(honda["name"], "Honda")
        self.assertEqual([model["slug"] for model in honda["models"]], ["city", "civic"])
        toyota_names = [model["name"] for model in catalogue["makes"][1]["models"]]
        self.assertIn("Land Cruiser", toyota_names)

    def test_model_records_keep_full_slug_and_path(self):
        catalogue = pw.parse_catalogue(CATALOGUE_PAGE)
        honda = catalogue["makes"][0]
        civic = next(model for model in honda["models"] if model["slug"] == "civic")
        self.assertEqual(civic["full_slug"], "honda-civic")
        self.assertEqual(civic["path"], "/used-cars/honda-civic/642")
        # repeated brand link (second carousel slide) must not duplicate models
        self.assertEqual(len([m for m in honda["models"] if m["slug"] == "civic"]), 1)

    def test_used_bike_links_are_ignored(self):
        catalogue = pw.parse_catalogue(CATALOGUE_PAGE)
        self.assertNotIn("cd-70", json.dumps(catalogue).lower())

    def test_fallback_catalogue_and_names(self):
        fallback = pw.fallback_catalogue()
        self.assertTrue(any(make["slug"] == "honda" for make in fallback["makes"]))
        self.assertEqual(pw.pretty_name("land-cruiser"), "Land Cruiser")
        self.assertEqual(pw.pretty_name("mercedes-benz"), "Mercedes Benz")


class ComplianceTests(unittest.TestCase):
    def test_allowed_urls(self):
        self.assertTrue(
            pw.is_url_allowed("https://www.pakwheels.com/used-cars/search/-/mk_honda/md_civic/")
        )
        self.assertTrue(
            pw.is_url_allowed(
                "https://www.pakwheels.com/used-cars/search/-/mk_honda/md_civic/?page=3"
            )
        )
        self.assertTrue(pw.is_url_allowed("https://www.pakwheels.com/used-cars/"))

    def test_disallowed_query_parameters(self):
        for url in (
            "https://www.pakwheels.com/used-cars/search/-/mk_honda/md_civic/?sortby=price_asc",
            "https://www.pakwheels.com/used-cars/search/-/mk_honda/md_civic/?nf=true",
        ):
            with self.assertRaises(pw.RobotsDisallowed):
                pw.assert_url_allowed(url)

    def test_disallowed_paths_and_hosts(self):
        for url in (
            "https://www.pakwheels.com/login",
            "https://www.pakwheels.com/users/123",
            "https://www.pakwheels.com/used-cars/details.json",
            "https://www.pakwheels.com/used-cars/lightbox_window/123",
            (
                "https://www.pakwheels.com/used-cars/honda-civic-1999-for-sale-in-lahore-12124197"
                "/seller_contact_info"
            ),
            "https://example.com/used-cars/",
            "ftp://www.pakwheels.com/used-cars/",
        ):
            with self.assertRaises(pw.RobotsDisallowed):
                pw.assert_url_allowed(url)

    def test_politeness_floor_cannot_be_disabled(self):
        self.assertEqual(pw.RateLimiter(0.1).min_delay, pw.MIN_DELAY)
        self.assertEqual(pw.PoliteSession(delay=0.1).delay, pw.MIN_DELAY)

    def test_search_url(self):
        self.assertEqual(
            pw.search_url("honda", "civic"),
            "https://www.pakwheels.com/used-cars/search/-/mk_honda/md_civic/",
        )


class EstimateTests(unittest.TestCase):
    def test_estimate_eta(self):
        self.assertEqual(pw.estimate_eta(5, 2.0, 1.5), round(5 * 3.65, 1))
        self.assertEqual(pw.estimate_eta(0, 2.0, 1.5), 0.0)

    def test_estimate_eta_respects_delay_floor(self):
        self.assertEqual(
            pw.estimate_eta(2, 0.0, 0.0), 2 * (pw.MIN_DELAY + pw.PAGE_OVERHEAD_S)
        )

    def test_human_duration(self):
        self.assertEqual(pw.human_duration(30), "30 s")
        self.assertEqual(pw.human_duration(95), "1 min 35 s")
        self.assertEqual(pw.human_duration(3700), "1 h 1 min")
        self.assertEqual(pw.human_duration(None), "unknown")


def _frame(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=pw.LISTING_COLUMNS)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        rows = pw.parse_listing_cards(pw.make_soup(RESULTS_PAGE), make="honda", model="civic")
        self.df = _frame(rows)

    def test_build_payload(self):
        payload = pw.build_scatter_payload(self.df, "honda", "civic")
        self.assertEqual(payload["stats"]["count"], 3)
        self.assertEqual(payload["stats"]["price_min"], 1_260_000)
        self.assertEqual(payload["stats"]["price_max"], 6_550_000)
        self.assertEqual(payload["facets"]["location"], ["Karachi", "Lahore"])
        self.assertIn("Petrol", payload["facets"]["fuel_type"])
        self.assertTrue(all(point["price_lacs"] for point in payload["points"]))
        self.assertEqual(payload["meta"]["make"], "honda")

    def test_payload_surfaces_fmv_and_deal_fields(self):
        payload = pw.build_scatter_payload(self.df, "honda", "civic")
        self.assertTrue(payload["points"])
        for point in payload["points"]:
            for key in (
                "expected_price_pkr", "expected_price_lacs", "residual_pkr",
                "z_score", "deal_rating", "fmv_method",
            ):
                self.assertIn(key, point)
            self.assertIsNotNone(point["expected_price_pkr"])
            self.assertAlmostEqual(
                point["expected_price_lacs"],
                round(point["expected_price_pkr"] / 100_000.0, 2),
                places=2,
            )
            self.assertAlmostEqual(
                point["residual_pkr"],
                point["price_pkr"] - point["expected_price_pkr"],
                places=2,
            )
            self.assertIn(point["deal_rating"], pw.DEAL_RATING_ORDER)
            self.assertIn(
                point["fmv_method"],
                {"variant_quadratic", "model_year_median", "model_median", "unavailable"},
            )

        stats = payload["stats"]
        self.assertEqual(sum(stats["deal_counts"].values()), stats["count"])
        self.assertEqual(
            stats["deal_rated_count"] + stats["deal_counts"]["Unknown"], stats["count"]
        )
        self.assertGreaterEqual(stats["fmv_available_count"], 1)
        self.assertTrue(set(payload["facets"]["deal_rating"]) <= set(pw.DEAL_RATING_ORDER))
        json.dumps(payload)  # payload must stay JSON serialisable for the API

    def test_payload_reuses_precomputed_fmv_columns(self):
        analysed = pw.calculate_fmv_and_deal_scores(self.df)
        payload = pw.build_scatter_payload(analysed, "honda", "civic")
        expected = {
            str(row.listing_id): round(float(row.expected_price_pkr), 2)
            for row in analysed.itertuples()
        }
        for point in payload["points"]:
            self.assertEqual(point["expected_price_pkr"], expected[point["listing_id"]])

    def test_linear_fit(self):
        self.assertIsNone(pw.linear_fit([2000, 2010], [10.0, 20.0]))
        fit = pw.linear_fit([2000, 2010, 2020], [10.0, 20.0, 30.0])
        self.assertAlmostEqual(fit["slope"], 1.0, places=6)
        self.assertAlmostEqual(fit["intercept"], -1990.0, places=6)
        self.assertAlmostEqual(fit["r_squared"], 1.0, places=6)
        self.assertEqual(fit["x_min"], 2000)

    def test_render_scatter_html_and_preview(self):
        payload = pw.build_scatter_payload(self.df, "honda", "civic")
        html = pw.render_scatter_html(payload, "Honda Civic test")
        self.assertIn("Honda Civic test", html)
        self.assertIn('id="chart"', html)
        self.assertIn("Plotly.newPlot", html)
        self.assertIn('"points"', html)
        preview = pw.dataframe_preview(self.df, limit=2)
        self.assertEqual(len(preview), 2)
        json.dumps(preview)  # must stay JSON serialisable

    def test_empty_dataframe(self):
        payload = pw.build_scatter_payload(
            pd.DataFrame(columns=pw.LISTING_COLUMNS), "honda", "civic"
        )
        self.assertEqual(payload["stats"]["count"], 0)
        self.assertEqual(payload["points"], [])
        self.assertEqual(set(payload["stats"]["deal_counts"]), set(pw.DEAL_RATING_ORDER))
        self.assertEqual(payload["stats"]["deal_rated_count"], 0)
        self.assertEqual(pw.dataframe_preview(None), [])

    def test_save_run_writes_csv_json_and_meta(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = pw.save_run(self.df, {"make": "honda", "model": "civic"}, Path(tmp))
            for key in ("csv", "json", "meta"):
                self.assertTrue(Path(paths[key]).exists(), key)
            csv_text = Path(paths["csv"]).read_text(encoding="utf-8-sig")
            self.assertIn("listing_id", csv_text)
            self.assertIn("12124197", csv_text)
            meta = json.loads(Path(paths["meta"]).read_text(encoding="utf-8"))
            self.assertEqual(meta["make"], "honda")

    def test_listing_columns_are_stable(self):
        self.assertEqual(list(self.df.columns), pw.LISTING_COLUMNS)
        self.assertEqual(len(self.df), 3)


def _listing_row(**overrides) -> dict:
    """One listing skeleton with every known column, then the given overrides."""
    row = {column: None for column in pw.LISTING_COLUMNS}
    row.update(overrides)
    return row


class FmvDealScoreTests(unittest.TestCase):
    """Feature 1.1: per-variant FMV curve, residuals, z-scores and deal labels."""

    @staticmethod
    def _perfect_fit_rows() -> tuple[list[dict], list[int]]:
        """Six listings that lie exactly on a quadratic price-vs-year curve.

        ``variant`` is deliberately blank so the engine has to fall back to
        ``variant_family`` when building the variant key.
        """
        prices = []
        rows = []
        for year in range(2015, 2021):
            offset = 2018 - year
            price = 5_000_000 - 200_000 * offset + 10_000 * offset ** 2
            prices.append(price)
            rows.append(
                _listing_row(
                    make="Honda",
                    model="Civic",
                    variant=None,
                    variant_family="VTi",
                    year=year,
                    price_pkr=price,
                )
            )
        return rows, prices

    def test_perfect_fit_recovers_curve_and_marks_fair_price(self):
        rows, prices = self._perfect_fit_rows()
        df = _frame(rows)
        snapshot = df.copy(deep=True)

        result = pw.calculate_fmv_and_deal_scores(df)

        self.assertEqual(len(result), len(rows))
        for expected, record in zip(prices, result.itertuples(index=False)):
            self.assertTrue(
                math.isclose(record.expected_price_pkr, expected, abs_tol=1.0),
                (record.year, record.expected_price_pkr, expected),
            )
            self.assertTrue(math.isclose(record.residual_pkr, 0.0, abs_tol=1.0))
            self.assertEqual(record.z_score, 0.0)
            self.assertEqual(record.deal_rating, "Fair Price")
            self.assertEqual(record.fmv_method, "variant_quadratic")
        # The caller's frame is never mutated (single rebuild path assumption).
        pd.testing.assert_frame_equal(df, snapshot)

        # Fit metadata: the synthetic prices are exactly quadratic.
        curve = pw._fit_variant_curve(range(2015, 2021), prices)
        self.assertEqual(curve["n"], 6)
        self.assertAlmostEqual(curve["r_squared"], 1.0, places=4)
        self.assertAlmostEqual(curve["rmse"], 0.0, places=2)
        self.assertIsNone(pw._fit_variant_curve(range(2015, 2019), [1, 2, 3, 4]))
        self.assertIsNone(
            pw._fit_variant_curve([2018] * 5, [4_000_000, 4_100_000, 4_200_000,
                                               4_300_000, 4_400_000])
        )

    def test_residuals_and_extreme_listings_get_deal_labels(self):
        years = list(range(2010, 2021))
        prices = [4_000_000 + (year - 2010) * 250_000 for year in years]
        prices[0] = 1_700_000  # far below the curve -> cheapest deal
        rows = [
            _listing_row(
                make="Toyota",
                model="Corolla",
                variant="GLi",
                variant_family="GLi",
                year=year,
                price_pkr=price,
            )
            for year, price in zip(years, prices)
        ]

        result = pw.calculate_fmv_and_deal_scores(_frame(rows))

        self.assertEqual(set(result["fmv_method"]), {"variant_quadratic"})
        cheapest = result.iloc[0]
        self.assertEqual(cheapest["deal_rating"], "Great Deal")
        self.assertLess(cheapest["z_score"], pw.GREAT_DEAL_Z)
        self.assertAlmostEqual(
            cheapest["residual_pkr"],
            cheapest["price_pkr"] - cheapest["expected_price_pkr"],
            places=2,
        )
        self.assertEqual(result.iloc[1]["deal_rating"], "Overpriced")
        self.assertGreater(result.iloc[1]["z_score"], pw.OVERPRICED_Z)
        self.assertTrue(result["z_score"].between(-10, 10).all())

    def test_small_variant_falls_back_to_model_year_median(self):
        # Five listings of a single model year cannot support a year curve.
        rows = [
            _listing_row(
                make="Honda", model="Civic", variant="VTi", variant_family="VTi",
                year=2018, price_pkr=price,
            )
            for price in (4_800_000, 5_000_000, 5_200_000, 5_400_000, 5_600_000)
        ]
        rows += [
            _listing_row(
                make="Honda", model="Civic", variant="Base", variant_family="Base",
                year=2018, price_pkr=price,
            )
            for price in (3_000_000, 4_000_000)
        ]
        df = _frame(rows)

        result = pw.calculate_fmv_and_deal_scores(df)

        self.assertEqual(set(result["fmv_method"]), {"model_year_median"})
        # Median of all seven 2018 prices is 5,000,000.
        self.assertTrue((result["expected_price_pkr"] == 5_000_000).all())
        spread = df["price_pkr"].astype(float).std(ddof=1)
        cheapest = result[result["variant"] == "Base"].iloc[0]
        self.assertAlmostEqual(
            cheapest["z_score"], round((3_000_000 - 5_000_000) / spread, 4), places=4
        )
    def test_fallback_to_whole_model_median_without_year_peer(self):
        rows = [
            _listing_row(
                make="Honda", model="Civic", variant="VTi", variant_family="VTi",
                year=2018, price_pkr=price,
            )
            for price in (4_800_000, 5_000_000, 5_200_000, 5_400_000, 5_600_000)
        ]
        rows += [
            _listing_row(
                make="Honda", model="Civic", variant="Base", variant_family="Base",
                year=2018, price_pkr=price,
            )
            for price in (3_000_000, 4_000_000)
        ]
        rows.append(
            _listing_row(
                make="Honda", model="Civic", variant="Base", variant_family="Base",
                year=2010, price_pkr=4_000_000,
            )
        )

        result = pw.calculate_fmv_and_deal_scores(_frame(rows))

        lone_year = result[result["year"] == 2010].iloc[0]
        # No other 2010 listing -> median of the whole model (4,900,000).
        self.assertEqual(lone_year["fmv_method"], "model_median")
        self.assertEqual(lone_year["expected_price_pkr"], 4_900_000)

    def test_deal_rating_thresholds(self):
        cases = [
            (-2.0, "Great Deal"),
            (-1.21, "Great Deal"),
            (-1.2, "Good Deal"),
            (-0.51, "Good Deal"),
            (-0.5, "Fair Price"),
            (0.0, "Fair Price"),
            (0.8, "Fair Price"),
            (0.81, "Overpriced"),
            (None, "Unknown"),
            (float("nan"), "Unknown"),
        ]
        for z_score, expected in cases:
            with self.subTest(z_score=z_score):
                self.assertEqual(pw.deal_rating_from_z(z_score), expected)

    def test_invalid_prices_stay_unknown_and_bad_year_uses_fallback(self):
        rows, _prices = self._perfect_fit_rows()
        rows += [
            _listing_row(
                make="Honda", model="Civic", variant="VTi", variant_family="VTi",
                year=2020, price_pkr=None,
            ),
            _listing_row(
                make="Honda", model="Civic", variant="VTi", variant_family="VTi",
                year=2020, price_pkr="price on request",
            ),
            _listing_row(
                make="Honda", model="Civic", variant="VTi", variant_family="VTi",
                year=1900, price_pkr=5_000_000,
            ),
        ]

        result = pw.calculate_fmv_and_deal_scores(_frame(rows))

        for index in (6, 7):
            self.assertEqual(result.loc[index, "deal_rating"], "Unknown")
            self.assertEqual(result.loc[index, "fmv_method"], "unavailable")
            self.assertTrue(pd.isna(result.loc[index, "expected_price_pkr"]))
        out_of_range_year = result.loc[8]
        self.assertEqual(out_of_range_year["fmv_method"], "model_median")
        self.assertEqual(out_of_range_year["expected_price_pkr"], 5_000_000)

    def test_missing_year_or_price_column_degrades_gracefully(self):
        no_price = pd.DataFrame({"listing_id": ["1"], "make": ["Honda"]})
        result = pw.calculate_fmv_and_deal_scores(no_price)
        self.assertEqual(result.loc[0, "deal_rating"], "Unknown")
        self.assertEqual(result.loc[0, "fmv_method"], "unavailable")
        self.assertTrue(pd.isna(result.loc[0, "expected_price_pkr"]))

        no_year = pd.DataFrame(
            {"listing_id": ["1", "2"], "price_pkr": [3_000_000, 5_000_000]}
        )
        result = pw.calculate_fmv_and_deal_scores(no_year)
        self.assertEqual(set(result["fmv_method"]), {"model_median"})
        self.assertTrue((result["expected_price_pkr"] == 4_000_000).all())

        # Nullable dtypes (``pd.NA``) must coerce cleanly rather than raise.
        nullable = pd.DataFrame(
            {
                "listing_id": ["1", "2"],
                "year": pd.array([2018, pd.NA], dtype="Int64"),
                "price_pkr": pd.array([3_000_000, pd.NA], dtype="Int64"),
            }
        )
        result = pw.calculate_fmv_and_deal_scores(nullable)
        self.assertEqual(result.loc[0, "fmv_method"], "model_median")
        self.assertEqual(result.loc[1, "deal_rating"], "Unknown")

        empty = pw.calculate_fmv_and_deal_scores(None)
        self.assertTrue(empty.empty)
        for column in pw.FMV_COLUMNS:
            self.assertIn(column, empty.columns)

    def test_preserves_index_and_is_idempotent(self):
        rows, _prices = self._perfect_fit_rows()
        df = _frame(rows)
        df.index = [101 + offset for offset in range(len(df))]

        first = pw.calculate_fmv_and_deal_scores(df)
        second = pw.calculate_fmv_and_deal_scores(first)

        self.assertListEqual(list(first.index), list(df.index))
        self.assertListEqual(list(second.index), list(df.index))
        pd.testing.assert_series_equal(
            first["expected_price_pkr"], second["expected_price_pkr"]
        )
        pd.testing.assert_series_equal(first["deal_rating"], second["deal_rating"])

    def test_listing_columns_expose_fmv_fields(self):
        for column in pw.FMV_COLUMNS:
            self.assertIn(column, pw.LISTING_COLUMNS)


class MultiModelPayloadTests(unittest.TestCase):
    """Feature 2.1: Multi-model targets, scraping delegates, payload & export contracts."""

    def test_normalize_targets(self):
        targets = [("honda", "civic"), ("toyota", "corolla")]
        self.assertEqual(
            pw.normalize_targets(targets),
            [("honda", "civic"), ("toyota", "corolla")],
        )

        dict_targets = [
            {"make": "Honda", "model": "Civic"},
            {"make": "Toyota", "model": "Corolla"},
        ]
        self.assertEqual(
            pw.normalize_targets(dict_targets),
            [("honda", "civic"), ("toyota", "corolla")],
        )

        duplicates = [("honda", "civic"), ("Honda", "Civic"), ("toyota", "corolla")]
        self.assertEqual(
            pw.normalize_targets(duplicates),
            [("honda", "civic"), ("toyota", "corolla")],
        )

        with self.assertRaises(ValueError):
            pw.normalize_targets([])

        with self.assertRaises(ValueError):
            pw.normalize_targets(None)

        with self.assertRaises(ValueError):
            pw.normalize_targets("honda-civic")

        with self.assertRaises(ValueError):
            pw.normalize_targets([
                ("honda", "civic"), ("toyota", "corolla"),
                ("suzuki", "alto"), ("kia", "sportage"), ("hyundai", "tucson")
            ])

    def test_comparison_label(self):
        self.assertEqual(
            pw.comparison_label([("honda", "civic"), ("toyota", "corolla")]),
            "Honda Civic vs Toyota Corolla",
        )
        self.assertEqual(
            pw.comparison_label([("honda", "civic")]),
            "Honda Civic",
        )
        self.assertEqual(pw.comparison_label([]), "")

    def test_multi_model_payload_formatting(self):
        civic_rows = [
            _listing_row(
                listing_id=101, make="honda", model="civic", title="Honda Civic 2020",
                year=2020, price_pkr=4500000, price_lacs=45.0, variant="VTi Oriel"
            ),
            _listing_row(
                listing_id=102, make="honda", model="civic", title="Honda Civic 2021",
                year=2021, price_pkr=5000000, price_lacs=50.0, variant="VTi Oriel"
            ),
        ]
        corolla_rows = [
            _listing_row(
                listing_id=201, make="toyota", model="corolla", title="Toyota Corolla 2019",
                year=2019, price_pkr=3800000, price_lacs=38.0, variant="GLi"
            ),
            _listing_row(
                listing_id=202, make="toyota", model="corolla", title="Toyota Corolla 2020",
                year=2020, price_pkr=4200000, price_lacs=42.0, variant="Altis"
            ),
            _listing_row(
                listing_id=203, make="toyota", model="corolla", title="Toyota Corolla 2021",
                year=2021, price_pkr=4600000, price_lacs=46.0, variant="Altis"
            ),
        ]
        df = _frame(civic_rows + corolla_rows)
        targets = [("honda", "civic"), ("toyota", "corolla")]

        payload = pw.build_scatter_payload(df, targets=targets)

        self.assertTrue(payload["meta"]["comparison"])
        self.assertEqual(payload["meta"]["make_model"], "Honda Civic vs Toyota Corolla")
        self.assertEqual(payload["meta"]["make_models"], ["Honda Civic", "Toyota Corolla"])
        self.assertEqual(
            payload["stats"]["make_model_counts"],
            {"Honda Civic": 2, "Toyota Corolla": 3},
        )
        self.assertEqual(payload["facets"]["make_model"], ["Honda Civic", "Toyota Corolla"])
        self.assertEqual(len(payload["points"]), 5)

        civic_points = [p for p in payload["points"] if p["make_model"] == "Honda Civic"]
        corolla_points = [p for p in payload["points"] if p["make_model"] == "Toyota Corolla"]
        self.assertEqual(len(civic_points), 2)
        self.assertEqual(len(corolla_points), 3)

        for p in civic_points:
            self.assertEqual(p["make"], "honda")
            self.assertEqual(p["model"], "civic")
            self.assertEqual(p["make_model"], "Honda Civic")

        for p in corolla_points:
            self.assertEqual(p["make"], "toyota")
            self.assertEqual(p["model"], "corolla")
            self.assertEqual(p["make_model"], "Toyota Corolla")

        json.dumps(payload)

    def test_multi_model_empty_dataframe_payload(self):
        empty_df = _frame([])
        targets = [("honda", "civic"), ("toyota", "corolla")]
        payload = pw.build_scatter_payload(empty_df, targets=targets)

        self.assertTrue(payload["meta"]["comparison"])
        self.assertEqual(payload["meta"]["make_model"], "Honda Civic vs Toyota Corolla")
        self.assertEqual(payload["meta"]["make_models"], ["Honda Civic", "Toyota Corolla"])
        self.assertEqual(payload["facets"]["make_model"], ["Honda Civic", "Toyota Corolla"])
        self.assertEqual(payload["points"], [])
        self.assertEqual(payload["stats"]["count"], 0)

    def test_multi_model_save_run_meta_and_exports(self):
        import tempfile

        civic_rows = [
            _listing_row(
                listing_id=101, make="honda", model="civic", title="Honda Civic 2020",
                year=2020, price_pkr=4500000, price_lacs=45.0
            ),
        ]
        corolla_rows = [
            _listing_row(
                listing_id=201, make="toyota", model="corolla", title="Toyota Corolla 2019",
                year=2019, price_pkr=3800000, price_lacs=38.0
            ),
        ]
        df = _frame(civic_rows + corolla_rows)
        targets = [("honda", "civic"), ("toyota", "corolla")]

        with tempfile.TemporaryDirectory() as tmp:
            paths = pw.save_run(df, {"targets": targets}, Path(tmp))
            for key in ("csv", "json", "meta"):
                self.assertTrue(Path(paths[key]).exists(), key)

            meta = json.loads(Path(paths["meta"]).read_text(encoding="utf-8"))
            self.assertTrue(meta["comparison"])
            self.assertEqual(meta["make_model"], "Honda Civic vs Toyota Corolla")
            self.assertEqual(meta["make_models"], ["Honda Civic", "Toyota Corolla"])
            self.assertEqual(
                meta["make_model_counts"],
                {"Honda Civic": 1, "Toyota Corolla": 1},
            )
            self.assertEqual(len(meta["targets"]), 2)
            self.assertEqual(meta["targets"][0]["make_model"], "Honda Civic")
            self.assertEqual(meta["targets"][1]["make_model"], "Toyota Corolla")

            csv_text = Path(paths["csv"]).read_text(encoding="utf-8-sig")
            self.assertIn("make_model", csv_text)
            self.assertIn("Honda Civic", csv_text)
            self.assertIn("Toyota Corolla", csv_text)

            json_data = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
            self.assertEqual(len(json_data), 2)
            self.assertEqual(json_data[0]["make_model"], "Honda Civic")
            self.assertEqual(json_data[1]["make_model"], "Toyota Corolla")

    def test_multi_model_render_scatter_html(self):
        civic_rows = [
            _listing_row(
                listing_id=101, make="honda", model="civic", title="Honda Civic 2020",
                year=2020, price_pkr=4500000, price_lacs=45.0
            ),
            _listing_row(
                listing_id=201, make="toyota", model="corolla", title="Toyota Corolla 2019",
                year=2019, price_pkr=3800000, price_lacs=38.0
            ),
        ]
        df = _frame(civic_rows)
        targets = [("honda", "civic"), ("toyota", "corolla")]
        payload = pw.build_scatter_payload(df, targets=targets)
        html = pw.render_scatter_html(payload, "Civic vs Corolla")

        self.assertIn("Civic vs Corolla", html)
        self.assertIn("make_model", html)
        self.assertIn("Plotly.newPlot", html)

    def test_scrape_methods_accept_targets(self):
        # Test scrape_pakwheels_model accepts targets list and delegating
        with self.assertRaises(ValueError):
            pw.scrape_pakwheels_model(targets=[])

        # Test invalid make/model raises when no targets
        with self.assertRaises(ValueError):
            pw.scrape_pakwheels_model(make=None, model=None)

        # Test scrape_pakwheels unified method
        with self.assertRaises(ValueError):
            pw.scrape_pakwheels(targets=[])


class SearchFilterTests(unittest.TestCase):
    def test_build_search_path_and_url_generic(self):
        filters = {
            "pr_min": 2000000,
            "pr_max": 3500000,
            "yr_min": 2015,
            "yr_max": 2020,
            "city": "islamabad",
        }
        url = pw.search_url(filters=filters)
        self.assertEqual(
            url,
            "https://www.pakwheels.com/used-cars/search/-/pr_2000000_3500000/yr_2015_2020/ct_islamabad/",
        )

    def test_build_search_path_all_filters(self):
        filters = {
            "make": "toyota",
            "model": "corolla",
            "km_min": 10000,
            "km_max": 50000,
            "cc_min": 1300,
            "cc_max": 1800,
            "transmission": "automatic",
            "fuel_type": "petrol",
            "body_type": "sedan",
            "seller_type": "individual",
            "color": "white",
        }
        url = pw.search_url(filters=filters)
        expected = (
            "https://www.pakwheels.com/used-cars/search/-/"
            "mk_toyota/md_corolla/km_10000_50000/cc_1300_1800/"
            "transmission_auto/fuel_petrol/bt_sedan/se_individual/cl_white/"
        )
        self.assertEqual(url, expected)


    def test_open_range_and_multi_select(self):
        filters = {
            'make': 'toyota',
            'model': 'corolla',
            'pr_min': 500000,
            'yr_min': 2010,
            'km_max': 150000,
            'city': ['islamabad', 'lahore'],
            'body_type': ['sedan', 'hatchback'],
            'exclude_keywords': ['baleno', 'cultus']
        }
        url = pw.search_url(filters=filters)
        expected = (
            'https://www.pakwheels.com/used-cars/search/-/'
            'mk_toyota/md_corolla/pr_500000_more/yr_2010_later/km_less_150000/'
            'ct_islamabad/ct_lahore/bt_sedan/bt_hatchback/'
        )
        self.assertEqual(url, expected)

    def test_dynamic_card_parsing_without_make_model(self):
        soup = pw.make_soup(RESULTS_PAGE)
        card = soup.select_one("li.classified-listing")
        parsed = pw.parse_listing_card(card, make=None, model=None)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["make"], "honda")
        self.assertEqual(parsed["model"], "civic")
        self.assertEqual(parsed["make_model"], "Honda Civic")
        self.assertEqual(parsed["year"], 1999)
        self.assertEqual(parsed["price_pkr"], 1260000.0)


class Task42GenericSearchAndArbitrageTests(unittest.TestCase):
    """Task 4.2: Generic Search Scraper & Cross-Model Normalization."""

    def test_dynamic_grouping_and_cross_model_fmv_math(self):
        """Mixed listings across makes/models are grouped and normalised correctly."""
        rows = [
            # Group 1: Toyota Corolla 2018 (N=4 >= 3)
            _listing_row(listing_id="1", make="Toyota", model="Corolla", year=2018, price_pkr=3_200_000),
            _listing_row(listing_id="2", make="Toyota", model="Corolla", year=2018, price_pkr=3_400_000),
            _listing_row(listing_id="3", make="Toyota", model="Corolla", year=2018, price_pkr=3_600_000),
            # Under-priced arbitrage listing: 2,400,000 (Z < -1.0)
            _listing_row(listing_id="4", make="Toyota", model="Corolla", year=2018, price_pkr=2_400_000),

            # Group 2: Honda Civic 2017 (N=3 >= 3)
            _listing_row(listing_id="5", make="Honda", model="Civic", year=2017, price_pkr=4_000_000),
            _listing_row(listing_id="6", make="Honda", model="Civic", year=2017, price_pkr=4_200_000),
            _listing_row(listing_id="7", make="Honda", model="Civic", year=2017, price_pkr=4_400_000),

            # Group 3: Suzuki Alto 2020 (N=3 >= 3)
            _listing_row(listing_id="8", make="Suzuki", model="Alto", year=2020, price_pkr=1_800_000),
            _listing_row(listing_id="9", make="Suzuki", model="Alto", year=2020, price_pkr=1_900_000),
            _listing_row(listing_id="10", make="Suzuki", model="Alto", year=2020, price_pkr=2_000_000),

            # Lone listing: Daihatsu Mira 2019 (N=1 < 3 fallback)
            _listing_row(listing_id="11", make="Daihatsu", model="Mira", year=2019, price_pkr=1_500_000),
        ]
        df = _frame(rows)

        result = pw.calculate_fmv_and_deal_scores(df)

        # 1. Verification of Toyota Corolla 2018 group
        corolla_2018 = result[(result["make"].str.lower() == "toyota") & (result["year"] == 2018)]
        self.assertEqual(len(corolla_2018), 4)
        # Median of (2400k, 3200k, 3400k, 3600k) is (3200k + 3400k)/2 = 3300k
        expected_corolla_fmv = 3_300_000.0
        for fmv_val in corolla_2018["fmv"]:
            self.assertEqual(fmv_val, expected_corolla_fmv)

        # Underpriced listing 4
        deal_car = corolla_2018[corolla_2018["listing_id"] == "4"].iloc[0]
        self.assertEqual(deal_car["pkr_below_fmv"], expected_corolla_fmv - 2_400_000.0)
        self.assertEqual(deal_car["pkr_below_fmv"], 900_000.0)
        self.assertAlmostEqual(deal_car["pct_below_fmv"], round((900_000.0 / 3_300_000.0) * 100.0, 2))
        self.assertLess(deal_car["z_score"], -1.0)
        self.assertTrue(pw.is_underpriced_outlier(deal_car["z_score"]))
        self.assertIn(deal_car["deal_rating"], ("Great Deal", "Good Deal"))

        # 2. Verification of Honda Civic 2017 group
        civic_2017 = result[(result["make"].str.lower() == "honda") & (result["year"] == 2017)]
        self.assertEqual(len(civic_2017), 3)
        # Median of (4000k, 4200k, 4400k) is 4200k
        for fmv_val in civic_2017["fmv"]:
            self.assertEqual(fmv_val, 4_200_000.0)

        # 3. Verification of Suzuki Alto 2020 group
        alto_2020 = result[(result["make"].str.lower() == "suzuki") & (result["year"] == 2020)]
        self.assertEqual(len(alto_2020), 3)
        # Median of (1800k, 1900k, 2000k) is 1900k
        for fmv_val in alto_2020["fmv"]:
            self.assertEqual(fmv_val, 1_900_000.0)

        # 4. Check all required arbitrage columns exist and are populated
        for col in ("fmv", "pkr_below_fmv", "pct_below_fmv", "z_score", "deal_rating"):
            self.assertIn(col, result.columns)
            self.assertTrue(result[col].notna().all())

    def test_dynamic_grouping_by_variant_year(self):
        """When group_by_variant=True, distinct variant-year groups are formed."""
        rows = [
            # 3 listings of VTi Oriel in 2018 (~55 lacs)
            _listing_row(listing_id="1", make="Honda", model="Civic", variant="VTi Oriel", year=2018, price_pkr=5_400_000),
            _listing_row(listing_id="2", make="Honda", model="Civic", variant="VTi Oriel", year=2018, price_pkr=5_500_000),
            _listing_row(listing_id="3", make="Honda", model="Civic", variant="VTi Oriel", year=2018, price_pkr=5_600_000),
            # 3 listings of 1.8 i-VTEC Base in 2018 (~45 lacs)
            _listing_row(listing_id="4", make="Honda", model="Civic", variant="Base", year=2018, price_pkr=4_400_000),
            _listing_row(listing_id="5", make="Honda", model="Civic", variant="Base", year=2018, price_pkr=4_500_000),
            _listing_row(listing_id="6", make="Honda", model="Civic", variant="Base", year=2018, price_pkr=4_600_000),
        ]
        df = _frame(rows)
        result = pw.calculate_fmv_and_deal_scores(df, group_by_variant=True)

        vti_rows = result[result["variant"] == "VTi Oriel"]
        base_rows = result[result["variant"] == "Base"]
        self.assertTrue((vti_rows["fmv"] == 5_500_000.0).all())
        self.assertTrue((base_rows["fmv"] == 4_500_000.0).all())
        self.assertEqual(set(vti_rows["fmv_method"]), {"variant_year_median"})
        self.assertEqual(set(base_rows["fmv_method"]), {"variant_year_median"})

    def test_group_listings_dynamically_helper(self):
        rows = [
            _listing_row(listing_id="1", make="Toyota", model="Corolla", year=2018, price_pkr=3_000_000),
            _listing_row(listing_id="2", make="Toyota", model="Corolla", year=2018, price_pkr=3_100_000),
            _listing_row(listing_id="3", make="Toyota", model="Corolla", year=2018, price_pkr=3_200_000),
            _listing_row(listing_id="4", make="Honda", model="Civic", year=2019, price_pkr=4_000_000),
        ]
        df = _frame(rows)
        groups = pw.group_listings_dynamically(df, min_group_size=3)
        self.assertEqual(len(groups), 1)
        key = list(groups.keys())[0]
        self.assertEqual(key, ("toyota", "corolla", 2018.0))
        self.assertEqual(len(groups[key]), 3)

    def test_arbitrage_helper_functions(self):
        self.assertTrue(pw.is_underpriced_outlier(-1.05))
        self.assertTrue(pw.is_underpriced_outlier(-2.5))
        self.assertFalse(pw.is_underpriced_outlier(-0.95))
        self.assertFalse(pw.is_underpriced_outlier(0.0))
        self.assertFalse(pw.is_underpriced_outlier(None))
        self.assertFalse(pw.is_underpriced_outlier(float("nan")))

        pkr_diff, pct_diff = pw.calculate_arbitrage_metrics(2_500_000, 3_000_000)
        self.assertEqual(pkr_diff, 500_000.0)
        self.assertAlmostEqual(pct_diff, 16.67, places=2)

        pkr_diff_neg, pct_diff_neg = pw.calculate_arbitrage_metrics(3_500_000, 3_000_000)
        self.assertEqual(pkr_diff_neg, -500_000.0)
        self.assertAlmostEqual(pct_diff_neg, -16.67, places=2)

    def test_generic_card_model_derivation_from_title_catalogue(self):
        """Card parser can deduce make and model for arbitrary catalogue models."""
        html = """
        <li class="classified-listing">
          <div class="search-title">
            <a class="ad-detail-path" href="/used-cars/ad-12345">
              Suzuki Alto VXR 2021 for Sale
            </a>
          </div>
          <div class="price-details generic-dark-grey">PKR 18.5 lacs</div>
          <ul class="search-vehicle-info-2">
            <li>2021</li>
            <li>25,000 km</li>
            <li>Petrol</li>
            <li>660 cc</li>
            <li>Manual</li>
          </ul>
        </li>
        """
        card = pw.make_soup(html).select_one("li.classified-listing")
        parsed = pw.parse_listing_card(card, make=None, model=None)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["make"], "suzuki")
        self.assertEqual(parsed["model"], "alto")
        self.assertEqual(parsed["variant"], "VXR")
        self.assertEqual(parsed["year"], 2021)
        self.assertEqual(parsed["price_pkr"], 1_850_000.0)


class Task43ArbitrageMatrixTests(unittest.TestCase):
    def test_discount_tier_label_classification(self):
        self.assertEqual(pw.discount_tier_label(25.0), "Deep Discount (>20%)")
        self.assertEqual(pw.discount_tier_label(15.0), "Strong Discount (10–20%)")
        self.assertEqual(pw.discount_tier_label(7.5), "Moderate Discount (5–10%)")
        self.assertEqual(pw.discount_tier_label(0.0), "Near FMV (±5%)")
        self.assertEqual(pw.discount_tier_label(-4.0), "Near FMV (±5%)")
        self.assertEqual(pw.discount_tier_label(-10.0), "Above FMV (>5%)")
        self.assertEqual(pw.discount_tier_label(None), "No FMV Data")
        self.assertEqual(pw.discount_tier_label(float("nan")), "No FMV Data")

    def test_derive_arbitrage_badges(self):
        # All three badges triggered
        badges = pw.derive_arbitrage_badges(20.0, -1.8, "Lahore")
        self.assertIn("High Margin", badges)
        self.assertIn("Urgent Sale Candidate", badges)
        self.assertIn("Location Arbitrage Potential", badges)

        # Only Location Arbitrage Potential (pct >= 8, z not < -1.5, has location)
        badges2 = pw.derive_arbitrage_badges(10.0, -0.9, "Islamabad")
        self.assertEqual(badges2, ["Location Arbitrage Potential"])

        # No location -> no Location Arbitrage Potential
        badges3 = pw.derive_arbitrage_badges(16.0, -0.5, None)
        self.assertEqual(badges3, ["High Margin"])

        # Fair price -> no badges
        self.assertEqual(pw.derive_arbitrage_badges(2.0, -0.2, "Karachi"), [])

    def test_payload_includes_discount_tier_and_arbitrage_badges(self):
        rows = [
            _listing_row(listing_id="1", make="Toyota", model="Corolla", year=2019, price_pkr=3_400_000, location="Lahore"),
            _listing_row(listing_id="2", make="Toyota", model="Corolla", year=2019, price_pkr=3_500_000, location="Lahore"),
            _listing_row(listing_id="3", make="Toyota", model="Corolla", year=2019, price_pkr=3_600_000, location="Karachi"),
            _listing_row(listing_id="4", make="Toyota", model="Corolla", year=2019, price_pkr=2_400_000, location="Islamabad"),
        ]
        df = _frame(rows)
        payload = pw.build_scatter_payload(df, make="toyota", model="corolla")
        self.assertIn("discount_tier", payload["facets"])
        for pt in payload["points"]:
            self.assertIn("discount_tier", pt)
            self.assertIn("arbitrage_badges", pt)
            self.assertIsInstance(pt["arbitrage_badges"], list)
        bargain = next(p for p in payload["points"] if p["listing_id"] == "4")
        self.assertEqual(bargain["discount_tier"], "Deep Discount (>20%)")
        self.assertIn("High Margin", bargain["arbitrage_badges"])
        self.assertIn("Location Arbitrage Potential", bargain["arbitrage_badges"])

    def test_save_run_includes_arbitrage_fields_in_csv_and_json(self):
        import tempfile
        rows = [
            _listing_row(listing_id="1", make="Toyota", model="Corolla", year=2019, price_pkr=3_400_000),
            _listing_row(listing_id="2", make="Toyota", model="Corolla", year=2019, price_pkr=3_500_000),
            _listing_row(listing_id="3", make="Toyota", model="Corolla", year=2019, price_pkr=3_600_000),
            _listing_row(listing_id="4", make="Toyota", model="Corolla", year=2019, price_pkr=2_400_000),
        ]
        df = _frame(rows)
        with tempfile.TemporaryDirectory() as tmp:
            paths = pw.save_run(df, {}, Path(tmp))
            csv_text = Path(paths["csv"]).read_text(encoding="utf-8-sig")
            for col in ("fmv", "pkr_below_fmv", "pct_below_fmv", "deal_rating"):
                self.assertIn(col, csv_text.splitlines()[0])
            records = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
            self.assertEqual(len(records), 4)
            for col in ("fmv", "pkr_below_fmv", "pct_below_fmv", "deal_rating"):
                self.assertIn(col, records[0])

    def test_render_scatter_html_includes_deal_rank_matrix(self):
        rows = [
            _listing_row(listing_id="1", make="Toyota", model="Corolla", year=2019, price_pkr=3_500_000),
            _listing_row(listing_id="2", make="Toyota", model="Corolla", year=2019, price_pkr=2_500_000),
        ]
        payload = pw.build_scatter_payload(_frame(rows), make="toyota", model="corolla")
        html = pw.render_scatter_html(payload, "Test Report")
        self.assertIn("Deal Rank &amp; Flipping Matrix", html)
        self.assertIn('id="matrixSort"', html)
        self.assertIn('id="matrixBody"', html)
        self.assertIn("renderMatrix", html)


if __name__ == "__main__":
    unittest.main()




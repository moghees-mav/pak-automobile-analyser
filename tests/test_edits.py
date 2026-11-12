"""Offline unit tests for the hand-correction layer.

Two things are covered: the value coercion / column-patcher in :mod:`pw_scraper`
and the ``POST /api/listings/<job_id>/edit`` endpoint in :mod:`web_app`, which
must keep the payload, the preview table and every run file in sync.

No network access and no real scrapes: the DataFrame is built in memory and the
Flask app is exercised through its test client.

Run with:  python -m unittest discover -s tests -v
or:        python -m pytest tests -q
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pw_scraper as pw  # noqa: E402
import web_app  # noqa: E402

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

CIVIC_ID = "12124197"
COROLLA_ID = "99887766"


def _listing(listing_id: str = CIVIC_ID, **overrides) -> dict:
    """One parsed listing, matching ``pw_scraper.LISTING_COLUMNS``."""
    row = {
        "listing_id": listing_id,
        "make": "honda",
        "model": "civic",
        "title": "Honda Civic 1999 VTi Oriel Automatic 1.6 for Sale",
        "variant": "VTi Oriel Automatic 1.6",
        "variant_family": "VTi Oriel",
        "year": 1999,
        "price_pkr": 1_260_000,
        "price_lacs": 12.6,
        "mileage_km": 210_000,
        "fuel_type": "Petrol",
        "engine_cc": 1600,
        "transmission": "Automatic",
        "location": "Lahore",
        "posted_text": "Updated 5 minutes ago",
        "is_featured": False,
        "url": (
            "https://www.pakwheels.com/used-cars/honda-civic-1999-for-sale-"
            f"in-lahore-{listing_id}"
        ),
    }
    row.update(overrides)
    return row


def _frame(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=pw.LISTING_COLUMNS)


CIVIC = _listing()
COROLLA = _listing(
    COROLLA_ID,
    make="toyota",
    model="corolla",
    title="Toyota Corolla 2021 Altis Grande 1.8 for Sale",
    variant="Altis Grande 1.8",
    variant_family="Altis Grande",
    year=2021,
    price_pkr=6_550_000,
    price_lacs=65.5,
    mileage_km=38_000,
    engine_cc=1800,
    location="Karachi",
)


# --------------------------------------------------------------------------- #
# Field specs
# --------------------------------------------------------------------------- #


class EditableFieldSpecTests(unittest.TestCase):
    def test_specs_cover_every_editable_field_in_order(self):
        specs = pw.editable_field_specs()
        self.assertEqual(
            [spec["key"] for spec in specs], [key for key, _label, _kind in pw.EDITABLE_FIELDS]
        )
        self.assertEqual(len(specs), 12)
        json.dumps(specs)  # must stay JSON serialisable (sent inside the payload)

    def test_numeric_specs_expose_limits_and_required_flag(self):
        by_key = {spec["key"]: spec for spec in pw.editable_field_specs()}
        self.assertEqual((by_key["year"]["min"], by_key["year"]["max"]), (1950, 2049))
        self.assertEqual(
            (by_key["price_pkr"]["min"], by_key["price_pkr"]["max"]), (1, 9_999_999_999)
        )
        self.assertEqual(
            (by_key["mileage_km"]["min"], by_key["mileage_km"]["max"]), (0, 5_000_000)
        )
        self.assertEqual((by_key["engine_cc"]["min"], by_key["engine_cc"]["max"]), (0, 30_000))
        self.assertTrue(by_key["price_pkr"]["required"])
        self.assertFalse(by_key["mileage_km"]["required"])
        self.assertEqual(by_key["fuel_type"]["type"], "text")
        self.assertEqual(by_key["is_featured"]["type"], "bool")
        self.assertNotIn("min", by_key["fuel_type"])

    def test_identity_and_derived_columns_are_read_only(self):
        for field in ("listing_id", "url", "make", "model", "price_lacs"):
            with self.subTest(field=field):
                self.assertNotIn(field, pw.EDITABLE_FIELD_TYPES)
                with self.assertRaises(ValueError) as ctx:
                    pw.coerce_editable_value(field, "1")
                self.assertIn("cannot be edited by hand", str(ctx.exception))


# --------------------------------------------------------------------------- #
# Value coercion
# --------------------------------------------------------------------------- #


class CoerceEditableValueTests(unittest.TestCase):
    def test_text_values_are_cleaned(self):
        self.assertEqual(pw.coerce_editable_value("title", "  Honda   Civic  "), "Honda Civic")
        self.assertEqual(pw.coerce_editable_value("fuel_type", "Petrol"), "Petrol")

    def test_blank_text_means_unknown(self):
        self.assertIsNone(pw.coerce_editable_value("location", "   "))
        self.assertIsNone(pw.coerce_editable_value("posted_text", ""))

    def test_int_values_accept_separators_and_floats(self):
        self.assertEqual(pw.coerce_editable_value("price_pkr", "1,25,000"), 125_000)
        self.assertEqual(pw.coerce_editable_value("mileage_km", " 180000 "), 180_000)
        self.assertEqual(pw.coerce_editable_value("engine_cc", 1600.0), 1600)
        self.assertEqual(pw.coerce_editable_value("year", 2021), 2021)

    def test_blank_ints_are_cleared_except_price(self):
        self.assertIsNone(pw.coerce_editable_value("year", ""))
        self.assertIsNone(pw.coerce_editable_value("mileage_km", None))
        self.assertIsNone(pw.coerce_editable_value("engine_cc", "   "))
        with self.assertRaises(ValueError) as ctx:
            pw.coerce_editable_value("price_pkr", "")
        self.assertIn("required", str(ctx.exception))

    def test_out_of_range_ints_are_rejected(self):
        cases = {
            "year": 1949,
            "price_pkr": 0,
            "mileage_km": 5_000_001,
            "engine_cc": 30_001,
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as ctx:
                    pw.coerce_editable_value(field, value)
                self.assertIn("must be between", str(ctx.exception))

    def test_non_integer_numbers_are_rejected(self):
        for raw in ("12.5", "abc", True):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError) as ctx:
                    pw.coerce_editable_value("mileage_km", raw)
                self.assertIn("whole number", str(ctx.exception))

    def test_bool_values(self):
        for raw in (True, "true", "YES", "1"):
            self.assertTrue(pw.coerce_editable_value("is_featured", raw))
        for raw in (False, "", None, "no", "off", "0"):
            self.assertFalse(pw.coerce_editable_value("is_featured", raw))
        with self.assertRaises(ValueError):
            pw.coerce_editable_value("is_featured", "maybe")

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            pw.coerce_editable_value("duplicates_dropped", 1)
        self.assertIn("editable:", str(ctx.exception))

    def test_json_scalar_handles_nan_and_numpy(self):
        import numpy as np

        self.assertIsNone(pw.json_scalar(float("nan")))
        self.assertIsNone(pw.json_scalar(None))
        self.assertIs(pw.json_scalar(True), True)
        self.assertEqual(pw.json_scalar(np.int64(5)), 5)
        self.assertEqual(pw.json_scalar(np.float64(12.6)), 12.6)
        self.assertEqual(pw.json_scalar("Lahore"), "Lahore")


# --------------------------------------------------------------------------- #
# apply_listing_edit
# --------------------------------------------------------------------------- #


class ApplyListingEditTests(unittest.TestCase):
    def setUp(self):
        self.df = _frame([CIVIC, COROLLA])

    def test_price_edit_rederives_lacs_and_leaves_original_untouched(self):
        edited, changes = pw.apply_listing_edit(self.df, CIVIC_ID, {"price_pkr": "1,350,000"})
        self.assertEqual(changes, [{"field": "price_pkr", "from": 1_260_000, "to": 1_350_000}])
        self.assertEqual(edited.at[0, "price_pkr"], 1_350_000)
        self.assertEqual(edited.at[0, "price_lacs"], 13.5)
        # the caller's frame must not be mutated (the UI keeps a pristine snapshot)
        self.assertEqual(self.df.at[0, "price_pkr"], 1_260_000)
        self.assertEqual(self.df.at[0, "price_lacs"], 12.6)
        # the other listing is never touched
        self.assertEqual(edited.at[1, "price_pkr"], 6_550_000)

    def test_multiple_fields_and_blank_clearing(self):
        edited, changes = pw.apply_listing_edit(
            self.df,
            CIVIC_ID,
            {"location": "  Islamabad ", "mileage_km": "", "is_featured": "yes"},
        )
        self.assertEqual(
            changes,
            [
                {"field": "location", "from": "Lahore", "to": "Islamabad"},
                {"field": "mileage_km", "from": 210_000, "to": None},
                {"field": "is_featured", "from": False, "to": True},
            ],
        )
        self.assertIsNone(edited.at[0, "mileage_km"])
        self.assertTrue(bool(edited.at[0, "is_featured"]))
        json.dumps(pw.dataframe_preview(edited))

    def test_variant_edit_refreshes_family_unless_family_was_edited(self):
        edited, changes = pw.apply_listing_edit(
            self.df, CIVIC_ID, {"variant": "VTi Oriel Prosmatec 1.6"}
        )
        self.assertEqual([change["field"] for change in changes], ["variant", "variant_family"])
        self.assertEqual(changes[1]["to"], "VTi Oriel Prosmatec")
        self.assertEqual(edited.at[0, "variant_family"], "VTi Oriel Prosmatec")

        edited, changes = pw.apply_listing_edit(
            self.df,
            CIVIC_ID,
            {"variant": "VTi Oriel Prosmatec 1.6", "variant_family": "Custom family"},
        )
        self.assertEqual(
            [change["field"] for change in changes], ["variant", "variant_family"]
        )
        self.assertEqual(edited.at[0, "variant_family"], "Custom family")

    def test_noop_edit_reports_no_changes(self):
        edited, changes = pw.apply_listing_edit(
            self.df, CIVIC_ID, {"price_pkr": 1_260_000, "year": "1999"}
        )
        self.assertEqual(changes, [])
        # nothing moved: same JSON-visible values for both rows
        self.assertEqual(pw.dataframe_preview(edited), pw.dataframe_preview(self.df))

    def test_bad_inputs_raise_value_error(self):
        cases = [
            (self.df, "", {"year": 2000}),
            (self.df, "00000000", {"year": 2000}),
            (self.df, CIVIC_ID, {}),
            (self.df, CIVIC_ID, None),
            (_frame([]), CIVIC_ID, {"year": 2000}),
            (self.df.drop(columns=["listing_id"]), CIVIC_ID, {"year": 2000}),
        ]
        for df, listing_id, updates in cases:
            with self.subTest(listing_id=listing_id, updates=updates):
                with self.assertRaises(ValueError):
                    pw.apply_listing_edit(df, listing_id, updates)

        with self.assertRaises(ValueError) as ctx:
            pw.apply_listing_edit(None, CIVIC_ID, {"year": 2000})
        self.assertIn("no listings", str(ctx.exception))

    def test_read_only_field_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            pw.apply_listing_edit(self.df, CIVIC_ID, {"price_lacs": 99})
        self.assertIn("cannot be edited by hand", str(ctx.exception))


# --------------------------------------------------------------------------- #
# HTTP endpoint
# --------------------------------------------------------------------------- #


class ListingEditEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = web_app.app.test_client()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.job = web_app.manager.create("honda", "civic", 1, 2.0, None, {})
        self.job.state = "done"
        self.job.pages_done = 1
        self.job.frame = _frame([CIVIC, COROLLA])
        self.job.meta = {
            "make": "honda",
            "model": "civic",
            "generated_at": "2026-01-01 12:00:00",
            "source_url": "https://www.pakwheels.com/used-cars/honda-civic",
        }
        self.job.title = "PakWheels: Honda Civic"
        self.job.run_dir = Path(self.tmp.name) / self.job.id
        web_app.refresh_job_outputs(self.job)

        self.url = f"/api/listings/{self.job.id}/edit"

    @staticmethod
    def _find_point(payload, listing_id):
        return next(point for point in payload["points"] if point["listing_id"] == listing_id)

    def test_json_body_edit_refreshes_payload_preview_and_files(self):
        response = self.client.post(
            self.url,
            json={
                "listing_id": CIVIC_ID,
                "fields": {"price_pkr": "1,350,000", "location": "  Islamabad "},
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()

        self.assertEqual(
            {change["field"]: (change["from"], change["to"]) for change in body["changes"]},
            {"price_pkr": (1_260_000, 1_350_000), "location": ("Lahore", "Islamabad")},
        )
        self.assertEqual(body["manual_edits"], 2)

        point = self._find_point(body["payload"], CIVIC_ID)
        self.assertEqual(point["price_pkr"], 1_350_000)
        self.assertEqual(point["price_lacs"], 13.5)
        self.assertEqual(point["location"], "Islamabad")
        self.assertEqual(body["payload"]["meta"]["manual_edits"], 2)
        self.assertEqual(body["payload"]["stats"]["count"], 2)

        preview = next(row for row in body["preview"] if row["listing_id"] == CIVIC_ID)
        self.assertEqual(preview["price_pkr"], 1_350_000)
        self.assertEqual(preview["price_lacs"], 13.5)

        csv_text = Path(self.job.files["csv"]).read_text(encoding="utf-8-sig")
        self.assertIn("1350000", csv_text)
        self.assertIn("Islamabad", csv_text)
        records = json.loads(Path(self.job.files["json"]).read_text(encoding="utf-8"))
        self.assertEqual(
            next(row["price_pkr"] for row in records if row["listing_id"] == CIVIC_ID),
            1_350_000,
        )
        chart_html = Path(self.job.files["html"]).read_text(encoding="utf-8")
        self.assertIn("1350000", chart_html)
        meta = json.loads(Path(self.job.files["meta"]).read_text(encoding="utf-8"))
        self.assertEqual(meta["listings"], 2)
        self.assertEqual(len(meta["manual_edits"]), 2)
        self.assertEqual(
            sorted(entry["field"] for entry in meta["manual_edits"]),
            ["location", "price_pkr"],
        )
        self.assertEqual(
            {entry["listing_id"] for entry in meta["manual_edits"]}, {CIVIC_ID}
        )
        self.assertTrue(all("at" in entry for entry in meta["manual_edits"]))

    def test_form_style_bodies_are_accepted(self):
        flat = self.client.post(self.url, data={"listing_id": CIVIC_ID, "mileage_km": "180000"})
        self.assertEqual(flat.status_code, 200)
        self.assertEqual(
            flat.get_json()["changes"],
            [{"field": "mileage_km", "from": 210_000, "to": 180_000}],
        )

        nested = self.client.post(
            self.url,
            data={"listing_id": CIVIC_ID, "fields": json.dumps({"engine_cc": 1800})},
        )
        self.assertEqual(nested.status_code, 200)
        self.assertEqual(
            nested.get_json()["changes"], [{"field": "engine_cc", "from": 1600, "to": 1800}]
        )

    def test_noop_edit_is_ok_and_logs_nothing(self):
        response = self.client.post(
            self.url, json={"listing_id": CIVIC_ID, "fields": {"year": 1999}}
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["changes"], [])
        self.assertEqual(body["manual_edits"], 0)
        self.assertEqual(self.job.manual_edits, [])

    def test_invalid_requests_return_400(self):
        cases = [
            {"fields": {"year": 2000}},  # no listing_id
            {"listing_id": CIVIC_ID},  # no fields
            {"listing_id": CIVIC_ID, "fields": {}},  # empty fields
            {"listing_id": CIVIC_ID, "fields": "not json"},  # unparsable fields
            {"listing_id": "00000000", "fields": {"year": 2000}},  # unknown listing
            {"listing_id": CIVIC_ID, "fields": {"year": 1900}},  # out of range
            {"listing_id": CIVIC_ID, "fields": {"price_pkr": ""}},  # price required
            {"listing_id": CIVIC_ID, "fields": {"price_lacs": 1}},  # read-only column
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post(self.url, json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())

        self.assertEqual(self.job.manual_edits, [])

    def test_unknown_job_and_unfinished_job_are_rejected(self):
        unknown = self.client.post(
            "/api/listings/deadbeef0000/edit",
            json={"listing_id": CIVIC_ID, "fields": {"year": 2000}},
        )
        self.assertEqual(unknown.status_code, 404)

        pending = web_app.manager.create("honda", "civic", 1, 2.0, None, {})
        unfinished = self.client.post(
            f"/api/listings/{pending.id}/edit",
            json={"listing_id": CIVIC_ID, "fields": {"year": 2000}},
        )
        self.assertEqual(unfinished.status_code, 409)
        self.assertIn("corrected once a run has finished", unfinished.get_json()["error"])

    def test_payload_exposes_editable_field_specs(self):
        specs = self.job.payload["editable_fields"]
        self.assertEqual(
            [spec["key"] for spec in specs], [key for key, _label, _kind in pw.EDITABLE_FIELDS]
        )

    def test_refresh_derives_fmv_for_payload_preview_and_exports(self):
        point = self._find_point(self.job.payload, CIVIC_ID)
        self.assertEqual(point["expected_price_pkr"], 1_260_000)
        self.assertEqual(point["expected_price_lacs"], 12.6)
        self.assertEqual(point["residual_pkr"], 0)
        self.assertEqual(point["deal_rating"], "Fair Price")
        self.assertEqual(point["fmv_method"], "model_median")

        preview = next(row for row in self.job.preview if row["listing_id"] == CIVIC_ID)
        self.assertIn("expected_price_pkr", preview)
        self.assertEqual(preview["deal_rating"], "Fair Price")

        csv_text = Path(self.job.files["csv"]).read_text(encoding="utf-8-sig")
        header = csv_text.splitlines()[0]
        for column in ("expected_price_pkr", "residual_pkr", "z_score", "deal_rating"):
            self.assertIn(column, header)

    def test_column_missing_from_frame_is_created_by_the_edit(self):
        frame = _frame([CIVIC, COROLLA]).drop(columns=["posted_text"])
        job = web_app.manager.create("honda", "civic", 1, 2.0, None, {})
        job.state = "done"
        job.frame = frame
        job.run_dir = Path(self.tmp.name) / job.id
        web_app.refresh_job_outputs(job)

        response = self.client.post(
            f"/api/listings/{job.id}/edit",
            json={"listing_id": CIVIC_ID, "fields": {"posted_text": "Updated today"}},
        )
        self.assertEqual(response.status_code, 200)
        edited = self._find_point(response.get_json()["payload"], CIVIC_ID)
        self.assertEqual(edited["posted_text"], "Updated today")

    def test_featured_flag_round_trips_through_the_payload(self):
        response = self.client.post(
            self.url, json={"listing_id": COROLLA_ID, "fields": {"is_featured": "yes"}}
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(self._find_point(body["payload"], COROLLA_ID)["is_featured"])
        self.assertFalse(self._find_point(body["payload"], CIVIC_ID)["is_featured"])
        self.assertEqual(body["payload"]["stats"]["featured_count"], 1)


class MultiModelEndpointTests(unittest.TestCase):
    """Test web_app targets payload parsing and multi-target job creation."""

    def setUp(self):
        self.client = web_app.app.test_client()

    def test_parse_multi_target_payload(self):
        with web_app.app.test_request_context(
            "/api/scrape",
            json={"targets": [{"make": "honda", "model": "civic"}, {"make": "toyota", "model": "corolla"}], "pages": 1, "delay": 2.0},
        ):
            targets, pages, delay = web_app._parse_request_payload()
            self.assertEqual(targets, [("honda", "civic"), ("toyota", "corolla")])
            self.assertEqual(pages, 1)
            self.assertEqual(delay, 2.0)

    def test_create_multi_target_job(self):
        targets = [("honda", "civic"), ("toyota", "corolla")]
        job = web_app.manager.create("honda", "civic", 1, 2.0, None, {}, targets=targets)
        self.assertEqual(job.target_pairs, targets)
        self.assertTrue(job.is_comparison)
        self.assertEqual(job.make_model, "Honda Civic vs Toyota Corolla")


if __name__ == "__main__":
    unittest.main()


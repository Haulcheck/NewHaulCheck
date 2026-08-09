"""
Iteration 13 tests: Fuel & Emissions CRUD + summary (MPG/CO2), Calendar Add PMI recurring
projection, Documents regenerate with version stamp, Documents link_url field.

Login: manager@haulcheck.co.uk / Seed-Fleet-2026!
Cleans up all TEST_ prefixed records via API after each test class.
"""
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
import requests


# ---------- Base URL / auth ----------
def _resolve_base_url() -> str:
    b = os.environ.get("REACT_APP_BACKEND_URL")
    if b:
        return b.rstrip("/")
    for line in Path("/app/frontend/.env").read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            return line.split("=", 1)[1].strip().strip('"').rstrip("/")
    raise RuntimeError("REACT_APP_BACKEND_URL not configured")


BASE_URL = _resolve_base_url()
API = f"{BASE_URL}/api"
SEED_EMAIL = "manager@haulcheck.co.uk"
SEED_PASSWORD = "Seed-Fleet-2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": SEED_EMAIL, "password": SEED_PASSWORD}, timeout=20)
    if r.status_code != 200:
        r = requests.post(f"{API}/auth/register", json={"email": SEED_EMAIL, "password": SEED_PASSWORD, "name": "Fleet Manager"}, timeout=20)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ============================================================
# Feature 1 : Fuel & Emissions
# ============================================================
class TestFuelAndEmissions:
    def test_requires_auth(self):
        assert requests.get(f"{API}/fuel", timeout=15).status_code == 401
        assert requests.get(f"{API}/fuel/summary", timeout=15).status_code == 401
        assert requests.post(f"{API}/fuel", json={"vehicle_reg": "X"}, timeout=15).status_code == 401

    def test_single_fill_has_no_mpg_but_has_co2(self, auth_headers):
        reg = f"TEST-F{uuid.uuid4().hex[:4].upper()}"
        payload = {
            "vehicle_reg": reg, "fill_date": date.today().isoformat(),
            "odometer": 100000, "diesel_litres": 90.9, "adblue_litres": 4.5,
            "cost": 150.00, "notes": "TEST first fill",
        }
        r = requests.post(f"{API}/fuel", json=payload, headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text
        rec = r.json()
        assert rec["vehicle_reg"] == reg
        assert rec["diesel_litres"] == 90.9
        assert rec["id"].startswith("fuel_")
        fid = rec["id"]

        # GET list — record should be present with mpg=None, co2 = 90.9 * 2.64 = 239.976 -> 240.0
        rows = requests.get(f"{API}/fuel", headers=auth_headers, timeout=15).json()
        row = next(x for x in rows if x["id"] == fid)
        assert row["mpg"] is None, "First fill for a vehicle has no attributable miles"
        assert row["miles"] is None
        assert row["co2_kg"] == round(90.9 * 2.64, 1)  # 240.0
        # Cleanup
        requests.delete(f"{API}/fuel/{fid}", headers=auth_headers, timeout=15)

    def test_second_fill_computes_mpg_and_summary_totals(self, auth_headers):
        """Add two fills for the SAME vehicle with increasing odometer -> MPG computed."""
        reg = f"TEST-F{uuid.uuid4().hex[:4].upper()}"
        # First fill (baseline)
        r1 = requests.post(f"{API}/fuel", json={
            "vehicle_reg": reg, "fill_date": (date.today() - timedelta(days=10)).isoformat(),
            "odometer": 100000, "diesel_litres": 100.0, "adblue_litres": 5.0, "cost": 165.0,
        }, headers=auth_headers, timeout=15)
        assert r1.status_code == 200
        f1 = r1.json()["id"]

        # Second fill: +500 miles on 90.9 litres -> 500 / (90.9/4.54609) = 25.0 mpg
        r2 = requests.post(f"{API}/fuel", json={
            "vehicle_reg": reg, "fill_date": date.today().isoformat(),
            "odometer": 100500, "diesel_litres": 90.9, "adblue_litres": 4.5, "cost": 149.99,
        }, headers=auth_headers, timeout=15)
        assert r2.status_code == 200
        f2 = r2.json()["id"]

        rows = requests.get(f"{API}/fuel", headers=auth_headers, timeout=15).json()
        first = next(x for x in rows if x["id"] == f1)
        second = next(x for x in rows if x["id"] == f2)
        assert first["mpg"] is None
        assert second["miles"] == 500.0
        assert second["mpg"] == 25.0
        assert second["co2_kg"] == round(90.9 * 2.64, 1)

        # Summary: only 2nd fill counted for avg-mpg; miles=500, litres=90.9
        summary = requests.get(f"{API}/fuel/summary", headers=auth_headers, timeout=15).json()
        veh = next(v for v in summary["vehicles"] if v["vehicle_reg"] == reg)
        assert veh["fills"] == 2
        assert veh["miles"] == 500.0
        assert veh["diesel_litres"] == round(100.0 + 90.9, 1)
        assert veh["avg_mpg"] == 25.0
        assert veh["cost_per_mile"] == round((165.0 + 149.99) / 500, 2)
        # co2 = (100 + 90.9) * 2.64 = 503.976 -> 504.0
        assert veh["co2_kg"] == round((100.0 + 90.9) * 2.64, 1)
        # Fleet totals include our two fills
        assert summary["totals"]["fills"] >= 2
        assert summary["totals"]["co2_tonnes"] is not None

        # Update: edit second fill's cost via PUT
        edit = {
            "vehicle_reg": reg, "fill_date": date.today().isoformat(),
            "odometer": 100500, "diesel_litres": 90.9, "adblue_litres": 4.5, "cost": 200.00,
            "notes": "TEST edited",
        }
        r = requests.put(f"{API}/fuel/{f2}", json=edit, headers=auth_headers, timeout=15)
        assert r.status_code == 200 and r.json()["ok"] is True

        # Verify updated
        rows = requests.get(f"{API}/fuel", headers=auth_headers, timeout=15).json()
        upd = next(x for x in rows if x["id"] == f2)
        assert upd["cost"] == 200.00

        # Cleanup
        for fid in (f1, f2):
            requests.delete(f"{API}/fuel/{fid}", headers=auth_headers, timeout=15)
        # confirm gone
        rows = requests.get(f"{API}/fuel", headers=auth_headers, timeout=15).json()
        assert not any(x["id"] in (f1, f2) for x in rows)

    def test_update_missing_returns_404(self, auth_headers):
        r = requests.put(f"{API}/fuel/nonexistent", json={"vehicle_reg": "X"}, headers=auth_headers, timeout=15)
        assert r.status_code == 404


# ============================================================
# Feature 2 : Calendar Add PMI (recurring projection)
# ============================================================
class TestCalendarPMIRecurring:
    def test_pmi_projects_multiple_events_at_frequency(self, auth_headers):
        """POST /pmi with 6-week frequency -> /calendar returns projected 'pmi_due' events every 6 weeks up to ~52-week horizon."""
        reg = f"TEST-PMI{uuid.uuid4().hex[:4].upper()}"
        first_due = (date.today() + timedelta(days=7)).isoformat()  # <=30d -> due_soon
        r = requests.post(f"{API}/pmi", json={
            "vehicle_reg": reg, "frequency_weeks": 6,
            "next_due": first_due, "inspector": "TEST Insp",
        }, headers=auth_headers, timeout=15)
        assert r.status_code == 200
        pid = r.json()["id"]

        try:
            cal = requests.get(f"{API}/calendar", headers=auth_headers, timeout=15).json()
            due_events = [e for e in cal if e.get("type") == "pmi_due" and reg in (e.get("title") or "")]
            # 52w / 6w = ~8.67 => 9 events (first + 8 later), capped at 26
            assert len(due_events) >= 8, f"Expected >=8 pmi_due events, got {len(due_events)}: {due_events}"

            # First event: due_soon (7d out)
            due_events.sort(key=lambda e: e["date"])
            assert due_events[0]["date"] == first_due
            assert due_events[0]["status"] == "due_soon"
            # Later events should be marked valid (planned in subtitle)
            for e in due_events[1:]:
                assert e["status"] == "valid"
                assert "planned" in (e.get("subtitle") or "")

            # Verify 6-week spacing between consecutive events
            for a, b in zip(due_events, due_events[1:]):
                d1 = date.fromisoformat(a["date"]); d2 = date.fromisoformat(b["date"])
                assert (d2 - d1).days == 42, f"Expected 42d gap (6w), got {(d2 - d1).days} between {a['date']} and {b['date']}"
        finally:
            requests.delete(f"{API}/pmi/{pid}", headers=auth_headers, timeout=15)

    def test_pmi_freq_4_weeks_gives_more_events(self, auth_headers):
        reg = f"TEST-PMI4{uuid.uuid4().hex[:4].upper()}"
        first_due = date.today().isoformat()  # today -> due_soon
        r = requests.post(f"{API}/pmi", json={
            "vehicle_reg": reg, "frequency_weeks": 4, "next_due": first_due,
        }, headers=auth_headers, timeout=15)
        assert r.status_code == 200
        pid = r.json()["id"]
        try:
            cal = requests.get(f"{API}/calendar", headers=auth_headers, timeout=15).json()
            due_events = [e for e in cal if e.get("type") == "pmi_due" and reg in (e.get("title") or "")]
            # 52w / 4w = 13
            assert len(due_events) >= 13, f"Expected >=13 events at 4w freq, got {len(due_events)}"
        finally:
            requests.delete(f"{API}/pmi/{pid}", headers=auth_headers, timeout=15)


# ============================================================
# Feature 3 : Documents regenerate + version stamp
# ============================================================
class TestDocumentRegenerate:
    def test_generate_then_regenerate_bumps_version(self, auth_headers):
        # 1. Generate v1
        gen_payload = {
            "template": "Warning Letter",
            "title": f"TEST Letter {uuid.uuid4().hex[:4]}",
            "recipient_name": "John Smith",
            "recipient_address": "1 High St, London",
            "subject": "TEST subject v1",
            "body": "This is the initial v1 body content.\n\nRegards.",
        }
        r = requests.post(f"{API}/documents/generate", json=gen_payload, headers=auth_headers, timeout=60)
        assert r.status_code == 200, r.text
        doc = r.json()
        did = doc["id"]
        assert doc["notes"].startswith("v1 · generated"), f"Expected 'v1 · generated', got {doc['notes']!r}"
        assert doc.get("letter_data", {}).get("version") == 1
        assert doc["reference"] == "TEST subject v1"
        assert doc["attachments"] and doc["attachments"][0]["content_type"] == "application/pdf"
        first_file_id = doc["attachments"][0]["file_id"]

        try:
            # 2. Regenerate with edited body -> v2
            edit_payload = {**gen_payload, "subject": "TEST subject v2", "body": "Edited body for v2.\n\nSecond paragraph."}
            r = requests.put(f"{API}/documents/{did}/regenerate", json=edit_payload, headers=auth_headers, timeout=60)
            assert r.status_code == 200, r.text
            assert r.json()["ok"] is True
            assert r.json()["version"] == 2

            # 3. Verify via GET /documents
            listing = requests.get(f"{API}/documents", headers=auth_headers, timeout=15).json()
            updated = next(x for x in listing if x["id"] == did)
            assert updated["notes"].startswith("v2 · updated"), f"Expected 'v2 · updated', got {updated['notes']!r}"
            assert updated["reference"] == "TEST subject v2"
            assert updated.get("letter_data", {}).get("version") == 2
            # Attachment must be a fresh PDF (new file_id, or at least not orphaned)
            assert updated["attachments"], "Regenerated doc must retain an attachment"
            new_file_id = updated["attachments"][0]["file_id"]
            assert new_file_id, "New attachment must have a file_id"
            # New PDF should be downloadable
            r = requests.get(f"{API}/files/{new_file_id}", headers={"Authorization": auth_headers["Authorization"]}, timeout=30)
            assert r.status_code == 200
            assert r.headers.get("Content-Type", "").startswith("application/pdf")

            # 4. Third regenerate -> v3
            r = requests.put(f"{API}/documents/{did}/regenerate", json={**edit_payload, "body": "v3 body"}, headers=auth_headers, timeout=60)
            assert r.status_code == 200
            assert r.json()["version"] == 3
            listing = requests.get(f"{API}/documents", headers=auth_headers, timeout=15).json()
            updated = next(x for x in listing if x["id"] == did)
            assert updated["notes"].startswith("v3 · updated")
        finally:
            requests.delete(f"{API}/documents/{did}", headers=auth_headers, timeout=15)

    def test_regenerate_missing_returns_404(self, auth_headers):
        r = requests.put(f"{API}/documents/nonexistent/regenerate", json={
            "template": "Warning Letter", "subject": "x", "body": "x",
        }, headers=auth_headers, timeout=15)
        assert r.status_code == 404

    def test_regenerate_requires_auth(self):
        r = requests.put(f"{API}/documents/anyid/regenerate", json={"template": "Warning Letter"}, timeout=15)
        assert r.status_code == 401


# ============================================================
# Feature 4 : Document link_url
# ============================================================
class TestDocumentLinkUrl:
    def test_create_document_with_link_url_persists(self, auth_headers):
        payload = {
            "title": f"TEST Doc Link {uuid.uuid4().hex[:4]}",
            "doc_type": "Operator Licence",
            "reference": "OB1234567",
            "expiry_date": (date.today() + timedelta(days=100)).isoformat(),
            "link_url": "https://example.com/my-doc-policy",
        }
        r = requests.post(f"{API}/documents", json=payload, headers=auth_headers, timeout=15)
        assert r.status_code == 200
        doc = r.json()
        did = doc["id"]
        assert doc.get("link_url") == payload["link_url"]

        try:
            # GET list, verify link_url persisted
            listing = requests.get(f"{API}/documents", headers=auth_headers, timeout=15).json()
            row = next(x for x in listing if x["id"] == did)
            assert row.get("link_url") == payload["link_url"]

            # Edit link_url
            new_url = "https://example.com/updated-link"
            r = requests.put(f"{API}/documents/{did}", json={**payload, "link_url": new_url}, headers=auth_headers, timeout=15)
            assert r.status_code == 200
            listing = requests.get(f"{API}/documents", headers=auth_headers, timeout=15).json()
            row = next(x for x in listing if x["id"] == did)
            assert row.get("link_url") == new_url

            # Clear link_url
            r = requests.put(f"{API}/documents/{did}", json={**payload, "link_url": ""}, headers=auth_headers, timeout=15)
            assert r.status_code == 200
            listing = requests.get(f"{API}/documents", headers=auth_headers, timeout=15).json()
            row = next(x for x in listing if x["id"] == did)
            assert row.get("link_url", "") == ""
        finally:
            requests.delete(f"{API}/documents/{did}", headers=auth_headers, timeout=15)


# ============================================================
# Regression smoke : Fleet / Documents / Calendar / Dashboard still load
# ============================================================
class TestRegressionSmoke:
    def test_core_endpoints_still_work(self, auth_headers):
        for ep in ("/vehicles", "/trailers", "/drivers", "/documents",
                   "/insurance", "/pmi", "/calendar", "/dashboard", "/fuel",
                   "/fuel/summary", "/tacho", "/training"):
            r = requests.get(f"{API}{ep}", headers=auth_headers, timeout=20)
            assert r.status_code == 200, f"{ep} failed: {r.status_code} {r.text[:200]}"

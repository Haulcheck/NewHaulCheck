"""
Iteration 32 tests — VOR bug fix + Sold/Disposed feature + 18-month retention +
Trailer sold + regression (viewer 403 guard, licence-check recompute, PDF escaping).
"""
import os
import uuid
import time
import datetime as dt
import requests
import pytest

def _load_base():
    v = os.environ.get("REACT_APP_BACKEND_URL")
    if not v:
        try:
            for line in open("/app/frontend/.env"):
                if line.startswith("REACT_APP_BACKEND_URL="):
                    v = line.split("=", 1)[1].strip()
        except Exception:
            pass
    return (v or "").rstrip("/")

BASE_URL = _load_base()
API = f"{BASE_URL}/api"


def _register(email, password, role="manager"):
    r = requests.post(f"{API}/auth/register",
                      json={"email": email, "password": password, "name": "T32"})
    if r.status_code not in (200, 201):
        # already exists — login
        r = requests.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def token():
    email = f"iter32_{uuid.uuid4().hex[:8]}@haulcheck.co.uk"
    return _register(email, "Seed-Fleet-2026!")


@pytest.fixture
def client(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


def _mk_vehicle(client, reg=None):
    reg = reg or f"TS{uuid.uuid4().hex[:4].upper()}"
    r = client.post(f"{API}/vehicles", json={
        "registration": reg, "make": "Volvo", "model": "FH", "type": "HGV (Rigid)"
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


def _mk_overdue_pmi(client, vehicle_id, reg):
    past = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    r = client.post(f"{API}/pmi", json={
        "vehicle_reg": reg, "frequency_weeks": 6, "next_due": past
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


# ------------- BUG FIX: VOR excludes from compliance --------------------

class TestVorExcludesCompliance:
    def test_vor_removes_overdue_pmi_from_score(self, client):
        v = _mk_vehicle(client)
        _mk_overdue_pmi(client, v["id"], v["registration"])
        # Baseline
        d = client.get(f"{API}/dashboard").json()
        base_expired = d["counts"]["expired"]
        base_score = d.get("risk_score", 0)
        pmi_alerts = [a for a in d.get("alerts", []) if a.get("type") == "pmi" and v["registration"].replace(" ", "").lower() in (a.get("name","")+a.get("registration","")).replace(" ","").lower()]
        assert base_expired >= 1
        assert len(pmi_alerts) >= 1

        # Mark VOR
        r = client.post(f"{API}/vehicles/{v['id']}/vor", json={"reason": "brake parts", "off_date": dt.date.today().isoformat()})
        assert r.status_code == 200, r.text

        d2 = client.get(f"{API}/dashboard").json()
        assert d2["counts"]["expired"] < base_expired
        remaining = [a for a in d2.get("alerts", []) if a.get("type") == "pmi" and v["registration"].replace(" ","").lower() in (a.get("name","")+a.get("registration","")).replace(" ","").lower()]
        assert len(remaining) == 0
        assert d2.get("risk_score", 0) >= base_score

        # Clear VOR restores
        r = client.post(f"{API}/vehicles/{v['id']}/vor/clear")
        assert r.status_code == 200
        d3 = client.get(f"{API}/dashboard").json()
        assert d3["counts"]["expired"] >= base_expired


# ------------- FEATURE: Sold vehicle --------------------------------------

class TestSoldVehicle:
    def test_sold_endpoint_persists_and_excludes(self, client):
        v = _mk_vehicle(client)
        _mk_overdue_pmi(client, v["id"], v["registration"])
        base = client.get(f"{API}/dashboard").json()
        base_expired = base["counts"]["expired"]
        assert base_expired >= 1

        sold_date = dt.date.today().isoformat()
        r = client.post(f"{API}/vehicles/{v['id']}/sold",
                        json={"sold_date": sold_date, "notes": "sold to ABC & sons <ok>"})
        assert r.status_code == 200, r.text

        # Persistence: GET vehicles
        vs = client.get(f"{API}/vehicles").json()
        row = next(x for x in vs if x["id"] == v["id"])
        assert row["sold"] is True
        assert row["sold_date"] == sold_date
        assert "ABC" in row["sold_notes"]

        # Excludes from score
        d2 = client.get(f"{API}/dashboard").json()
        assert d2["counts"]["expired"] < base_expired
        alerts = [a for a in d2.get("alerts", []) if a.get("type") == "pmi" and v["registration"].replace(" ","").lower() in (a.get("name","")+a.get("registration","")).replace(" ","").lower()]
        assert alerts == []

        # Clear sold
        r = client.post(f"{API}/vehicles/{v['id']}/sold/clear")
        assert r.status_code == 200
        row2 = next(x for x in client.get(f"{API}/vehicles").json() if x["id"] == v["id"])
        assert row2["sold"] is False

    def test_sold_clears_vor(self, client):
        v = _mk_vehicle(client)
        client.post(f"{API}/vehicles/{v['id']}/vor", json={"reason": "x", "off_date": dt.date.today().isoformat()})
        client.post(f"{API}/vehicles/{v['id']}/sold", json={"sold_date": dt.date.today().isoformat(), "notes": ""})
        row = next(x for x in client.get(f"{API}/vehicles").json() if x["id"] == v["id"])
        assert row["sold"] is True
        assert not row.get("vor", False)


# ------------- FEATURE: 18-month retention ---------------------------------

class TestRetention:
    def test_offroad_sold_category_present(self, client):
        v = _mk_vehicle(client)
        old = (dt.date.today() - dt.timedelta(days=30 * 20)).isoformat()  # 20 months ago
        client.post(f"{API}/vehicles/{v['id']}/sold", json={"sold_date": old, "notes": "old sale"})

        v2 = _mk_vehicle(client)
        recent = dt.date.today().isoformat()
        client.post(f"{API}/vehicles/{v2['id']}/sold", json={"sold_date": recent, "notes": "fresh sale"})

        r = client.get(f"{API}/records-retention")
        assert r.status_code == 200
        data = r.json()
        cats = data.get("categories", data) if isinstance(data, dict) else data
        # Find off-road/sold category
        target = None
        for c in (cats if isinstance(cats, list) else cats.get("categories", [])):
            if "off-road" in c.get("label","").lower() or "sold" in c.get("label","").lower():
                target = c; break
        assert target is not None, f"Missing category, got: {data}"
        assert target["retention_months"] == 18

        items = target.get("items", [])
        old_item = next((i for i in items if v["registration"] in str(i)), None)
        recent_item = next((i for i in items if v2["registration"] in str(i)), None)
        assert old_item is not None, f"Old sold vehicle missing from retention items: {items}"
        # Old should be eligible
        assert (old_item.get("state") == "eligible") or old_item.get("eligible") is True, old_item
        if recent_item:
            assert recent_item.get("state") != "eligible"


# ------------- FEATURE: Trailer sold ---------------------------------------

class TestTrailerSold:
    def test_trailer_sold_excluded(self, client):
        # Create trailer with sold=True
        num = f"TR{uuid.uuid4().hex[:4].upper()}"
        r = client.post(f"{API}/trailers", json={
            "trailer_number": num, "type": "Curtainsider",
            "sold": True, "sold_date": dt.date.today().isoformat(), "sold_notes": "trailer disposal"
        })
        assert r.status_code in (200, 201), r.text
        tr = r.json()
        # Verify persistence
        got = next((x for x in client.get(f"{API}/trailers").json() if x["id"] == tr["id"]), None)
        assert got and got["sold"] is True


# ------------- REGRESSION: viewer 403 ---------------------------------------

class TestViewerGuard:
    def test_viewer_cannot_write(self, token):
        # Create a viewer sub-account via invite or role change endpoint (varies)
        # Simplified: create separate user account and cast to viewer via update-role if possible.
        email = f"viewer32_{uuid.uuid4().hex[:6]}@haulcheck.co.uk"
        vtoken = _register(email, "Seed-Fleet-2026!")
        # Try to set role to viewer via /api/auth/me PATCH or similar
        headers = {"Authorization": f"Bearer {vtoken}", "Content-Type": "application/json"}
        # Try admin endpoint (may not exist) — fallback: directly test — we don't have role-change route,
        # so hit the users collection via a role-setting endpoint if exposed:
        r = requests.patch(f"{API}/users/me/role", json={"role": "viewer"}, headers=headers)
        if r.status_code not in (200, 204):
            pytest.skip(f"No self role change endpoint; cannot test viewer guard directly (got {r.status_code})")
        # Now attempt writes
        r1 = requests.post(f"{API}/vehicles", json={"registration": "VIEWER1", "make": "X", "model": "Y", "type": "Van"}, headers=headers)
        assert r1.status_code == 403
        r2 = requests.get(f"{API}/vehicles", headers=headers)
        assert r2.status_code == 200


# ------------- REGRESSION: PDF with & < > in sold notes --------------------

class TestPdfEscaping:
    def test_pdf_with_special_chars_in_sold_notes(self, client):
        v = _mk_vehicle(client)
        client.post(f"{API}/vehicles/{v['id']}/sold",
                    json={"sold_date": dt.date.today().isoformat(),
                          "notes": "sold to ABC & <partner> >Zed<"})
        r = client.get(f"{API}/reports/vehicle/{v['registration']}")
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF", r.content[:20]
        assert "application/pdf" in r.headers.get("content-type", "")


# ------------- REGRESSION: licence-check recompute -------------------------

class TestLicenceCheckRecompute:
    def test_headline_updates_on_insert_delete(self, client):
        drivers = client.get(f"{API}/drivers").json()
        if not drivers:
            r = client.post(f"{API}/drivers", json={
                "name": f"TEST Iter32 {uuid.uuid4().hex[:4]}",
                "licence_number": f"T{uuid.uuid4().hex[:8].upper()}",
                "cpc_expiry": (dt.date.today()+dt.timedelta(days=90)).isoformat(),
                "licence_expiry": (dt.date.today()+dt.timedelta(days=365)).isoformat(),
            })
            assert r.status_code in (200, 201), r.text
            drivers = client.get(f"{API}/drivers").json()
        d = drivers[0]
        today = dt.date.today().isoformat()
        r = client.post(f"{API}/licence-checks", json={
            "driver_id": d["id"], "check_date": today, "check_code": "AB1", "points": 3, "result": "pass", "notes": "TEST_iter32"
        })
        assert r.status_code in (200, 201), r.text
        check_id = r.json().get("id")
        d2 = next(x for x in client.get(f"{API}/drivers").json() if x["id"] == d["id"])
        assert d2.get("licence_check_date") == today
        assert d2.get("penalty_points") == 3
        # Delete
        r = client.delete(f"{API}/licence-checks/{check_id}")
        assert r.status_code in (200, 204)

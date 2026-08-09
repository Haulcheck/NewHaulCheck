"""Tests for iter31 features: licence-checks, vehicle history pack, records-retention, test-history summary."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://transport-verify-3.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

EMAIL = "manager@haulcheck.co.uk"
PWD = "Seed-Fleet-2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PWD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def s(token):
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return sess


@pytest.fixture(scope="module")
def a_driver(s):
    r = s.get(f"{API}/drivers", timeout=15)
    assert r.status_code == 200
    drivers = r.json()
    assert len(drivers) > 0, "no drivers seeded"
    return drivers[0]


@pytest.fixture(scope="module")
def a_vehicle(s):
    r = s.get(f"{API}/vehicles", timeout=15)
    assert r.status_code == 200
    vs = r.json()
    assert len(vs) > 0
    return vs[0]


# ---------- FEATURE A: licence-checks ----------
class TestLicenceChecks:
    def test_list_licence_checks(self, s):
        r = s.get(f"{API}/licence-checks", timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_licence_check_syncs_driver(self, s, a_driver):
        did = a_driver["id"]
        payload = {
            "driver_id": did,
            "driver_name": a_driver.get("name", "Test"),
            "check_date": "2026-01-05",
            "check_code": "TEST_LC01",
            "points": 6,
            "result": "clean",
            "next_check_due": "2026-07-05",
            "notes": "TEST_ pytest iter31",
        }
        r = s.post(f"{API}/licence-checks", json=payload, timeout=15)
        assert r.status_code in (200, 201), r.text
        body = r.json()
        # id
        assert "id" in body or "_id" in body or body.get("driver_id") == did
        # GET-verify sync on driver
        r2 = s.get(f"{API}/drivers", timeout=15)
        drv = next((d for d in r2.json() if d["id"] == did), None)
        assert drv is not None
        assert str(drv.get("penalty_points")) == "6"
        assert drv.get("licence_check_code") == "TEST_LC01"
        assert drv.get("licence_check_date") == "2026-01-05"
        assert drv.get("licence_check_due") == "2026-07-05"
        # list contains
        r3 = s.get(f"{API}/licence-checks", timeout=15)
        assert any(lc.get("check_code") == "TEST_LC01" for lc in r3.json())
        # save id for delete
        lc = next((x for x in r3.json() if x.get("check_code") == "TEST_LC01"), None)
        assert lc is not None
        TestLicenceChecks._lc_id = lc.get("id") or lc.get("_id")

    def test_delete_licence_check(self, s):
        lcid = getattr(TestLicenceChecks, "_lc_id", None)
        assert lcid, "no lc id captured"
        r = s.delete(f"{API}/licence-checks/{lcid}", timeout=15)
        assert r.status_code in (200, 204), r.text
        r2 = s.get(f"{API}/licence-checks", timeout=15)
        assert not any((x.get("id") == lcid or x.get("_id") == lcid) for x in r2.json())


# ---------- FEATURE B: vehicle history pack ----------
class TestVehicleHistoryPack:
    def test_pdf_summary(self, s, a_vehicle):
        reg = a_vehicle["registration"]
        r = s.get(f"{API}/reports/vehicle/{reg}", timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert r.content[:4] == b"%PDF"

    def test_pdf_include_files(self, s, a_vehicle):
        reg = a_vehicle["registration"]
        r = s.get(f"{API}/reports/vehicle/{reg}?include_files=true", timeout=45)
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"

    def test_json_report_sections(self, s, a_vehicle):
        reg = a_vehicle["registration"]
        r = s.get(f"{API}/reports/vehicle/{reg}?format=json", timeout=30)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body.get("title", "").startswith("Vehicle History Pack")
        assert reg in body["title"]
        assert "sections" in body
        assert len(body["sections"]) >= 5

    def test_case_space_insensitive(self, s, a_vehicle):
        reg = a_vehicle["registration"]
        # mangle case+spaces
        mangled = reg.replace(" ", "").lower()
        r = s.get(f"{API}/reports/vehicle/{mangled}?format=json", timeout=30)
        assert r.status_code == 200
        # also spaced-inserted
        mangled2 = (reg[:4] + " " + reg[4:]).upper() if len(reg) >= 4 else reg.upper()
        r2 = s.get(f"{API}/reports/vehicle/{mangled2}?format=json", timeout=30)
        assert r2.status_code in (200, 404)  # if it made an odd form

    def test_unknown_reg_404(self, s):
        r = s.get(f"{API}/reports/vehicle/ZZ99ZZZ?format=json", timeout=15)
        assert r.status_code == 404


# ---------- FEATURE C: records retention ----------
class TestRecordsRetention:
    def test_retention_shape(self, s):
        r = s.get(f"{API}/records-retention", timeout=15)
        assert r.status_code == 200, r.text
        b = r.json()
        assert "total_eligible" in b
        assert "total_approaching" in b
        assert "categories" in b
        assert isinstance(b["categories"], list)
        # Not an exact count: iteration 32 added "Off-road / sold vehicles", and
        # pinning the number here means every new category breaks a test that was
        # never about the number. The four below are what this test is asserting;
        # the fifth has its own test in test_iter32_vor_sold.py.
        assert len(b["categories"]) >= 4
        labels = [c["label"] for c in b["categories"]]
        # the four categories this test covers
        for expected in ("PMI", "walkaround", "defect", "achograph"):
            assert any(expected.lower() in lb.lower() for lb in labels), f"missing {expected} in {labels}"
        for c in b["categories"]:
            assert "retention_months" in c
            assert "total" in c
            assert "eligible" in c
            assert "approaching" in c


# ---------- FEATURE D: test-history summary ----------
# The summary is computed client-side from GET /api/test-history. Verify data source.
class TestTestHistoryData:
    def test_get_test_history(self, s):
        r = s.get(f"{API}/test-history", timeout=15)
        # Endpoint may be /test-history or /annual-tests
        if r.status_code == 404:
            r = s.get(f"{API}/annual-tests", timeout=15)
        assert r.status_code == 200, r.text[:200]
        assert isinstance(r.json(), list)

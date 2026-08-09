"""
Iteration 30 tests — Viewer role 403, Weekly mid-week start, VOR endpoints, Repairs CRUD.
"""
import os
import uuid
import pytest
import requests
from datetime import date, timedelta
from pymongo import MongoClient

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

_run = uuid.uuid4().hex[:6]
state = {}


def _mongo():
    return MongoClient(MONGO_URL)[DB_NAME]


def _register(email, password="Seed-Fleet-2026!", name="Owner"):
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": password, "name": name})
    if r.status_code == 400:
        r = requests.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"], r.json()["user"]


@pytest.fixture(scope="module")
def owner():
    email = f"test_owner_{_run}@haulcheck.co.uk"
    tok, u = _register(email, name="Test Owner")
    return {"email": email, "token": tok, "user": u}


@pytest.fixture(scope="module")
def owner_vehicle(owner):
    h = {"Authorization": f"Bearer {owner['token']}"}
    reg = f"TT{_run.upper()[:4]}"
    r = requests.post(f"{API}/vehicles", json={"registration": reg, "make": "Volvo", "model": "FH"}, headers=h)
    assert r.status_code == 200, r.text
    v = r.json()
    return v


# ---------- Viewer role ----------
class TestViewerRole:
    def test_a_invite_viewer_and_accept(self, owner):
        h = {"Authorization": f"Bearer {owner['token']}"}
        viewer_email = f"test_viewer_{_run}@haulcheck.co.uk"
        # kind="org" is required and is not the default. Without it the invite is
        # a "referral", which deliberately gives the invitee a separate
        # organisation of their own as owner -- so the role above is ignored and
        # nothing is shared. This test is about joining the inviter's org.
        r = requests.post(f"{API}/invitations",
                          json={"email": viewer_email, "role": "viewer",
                                "kind": "org", "base_url": BASE}, headers=h)
        assert r.status_code == 200, r.text
        inv = _mongo().invitations.find_one({"email": viewer_email})
        assert inv and inv["role"] == "viewer", f"invite not stored as viewer: {inv}"
        token = inv["token"]
        rv = requests.get(f"{API}/invitations/verify", params={"token": token})
        assert rv.status_code == 200
        ra = requests.post(f"{API}/auth/accept-invite",
                           json={"token": token, "name": "Viewer User", "password": "Seed-Fleet-2026!"})
        assert ra.status_code == 200, ra.text
        data = ra.json()
        # Two different things are called "role". `role` is the account type --
        # manager or driver -- and accept-invite always issues "manager" because
        # the invitee is not a driver. The permission that matters is `org_role`,
        # which the org layer added after this test was written.
        assert data["user"]["role"] == "manager"
        assert data["user"]["org_role"] == "viewer"
        state["viewer_token"] = data["token"]
        state["viewer_email"] = viewer_email

    def test_b_viewer_sees_owner_vehicles(self, owner_vehicle):
        h = {"Authorization": f"Bearer {state['viewer_token']}"}
        r = requests.get(f"{API}/vehicles", headers=h)
        assert r.status_code == 200
        regs = [v["registration"] for v in r.json()]
        assert owner_vehicle["registration"] in regs, f"Viewer should see owner vehicle. Got {regs}"

    def test_c_viewer_post_vehicle_403(self):
        h = {"Authorization": f"Bearer {state['viewer_token']}"}
        r = requests.post(f"{API}/vehicles", json={"registration": "VIEW01", "make": "X"}, headers=h)
        assert r.status_code == 403, f"expected 403 got {r.status_code} {r.text}"

    def test_d_viewer_put_vehicle_403(self, owner_vehicle):
        h = {"Authorization": f"Bearer {state['viewer_token']}"}
        r = requests.put(f"{API}/vehicles/{owner_vehicle['id']}",
                         json={"registration": owner_vehicle["registration"], "make": "Nope"}, headers=h)
        assert r.status_code == 403

    def test_e_viewer_delete_vehicle_403(self, owner_vehicle):
        h = {"Authorization": f"Bearer {state['viewer_token']}"}
        r = requests.delete(f"{API}/vehicles/{owner_vehicle['id']}", headers=h)
        assert r.status_code == 403

    def test_f_viewer_write_repair_403(self):
        h = {"Authorization": f"Bearer {state['viewer_token']}"}
        r = requests.post(f"{API}/repairs", json={"vehicle_reg": "TT01", "description": "x"}, headers=h)
        assert r.status_code == 403

    def test_g_viewer_weekly_write_403(self):
        h = {"Authorization": f"Bearer {state['viewer_token']}"}
        r = requests.post(f"{API}/weekly-walkarounds",
                          json={"vehicle_reg": "X", "week_start": "2026-06-16", "driver_name": "D"}, headers=h)
        assert r.status_code == 403

    def test_h_viewer_vor_write_403(self, owner_vehicle):
        h = {"Authorization": f"Bearer {state['viewer_token']}"}
        r = requests.post(f"{API}/vehicles/{owner_vehicle['id']}/vor",
                          json={"reason": "x", "off_date": "2026-01-01"}, headers=h)
        assert r.status_code == 403

    def test_i_owner_manager_not_blocked(self, owner):
        h = {"Authorization": f"Bearer {owner['token']}"}
        reg = f"MG{_run.upper()[:4]}"
        r = requests.post(f"{API}/vehicles", json={"registration": reg, "make": "DAF"}, headers=h)
        assert r.status_code == 200, r.text
        requests.delete(f"{API}/vehicles/{r.json()['id']}", headers=h)

    def test_j_normal_manager_invite_still_works(self, owner):
        h = {"Authorization": f"Bearer {owner['token']}"}
        email = f"test_mgr_{_run}@x.com"
        r = requests.post(f"{API}/invitations", json={"email": email, "role": "manager", "base_url": BASE}, headers=h)
        assert r.status_code == 200, r.text
        inv = _mongo().invitations.find_one({"email": email})
        assert inv is not None, f"invitation not stored for {email}"
        assert inv["role"] == "manager"


# ---------- Weekly mid-week start ----------
class TestWeeklyMidWeekStart:
    def test_tuesday_not_snapped(self, owner, owner_vehicle):
        h = {"Authorization": f"Bearer {owner['token']}"}
        ws = "2026-06-16"  # Tuesday
        r = requests.post(f"{API}/weekly-walkarounds",
                          json={"vehicle_reg": owner_vehicle["registration"], "week_start": ws, "driver_name": "T Driver"},
                          headers=h)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["week_start"] == ws, f"Expected {ws} preserved, got {data['week_start']}"
        requests.delete(f"{API}/weekly-walkarounds/{data['id']}", headers=h)

    def test_thursday_not_snapped(self, owner, owner_vehicle):
        h = {"Authorization": f"Bearer {owner['token']}"}
        ws = "2026-08-13"  # Thursday
        r = requests.post(f"{API}/weekly-walkarounds",
                          json={"vehicle_reg": owner_vehicle["registration"], "week_start": ws, "driver_name": "T2"},
                          headers=h)
        assert r.status_code == 200
        assert r.json()["week_start"] == ws
        requests.delete(f"{API}/weekly-walkarounds/{r.json()['id']}", headers=h)

    def test_pdf_generates(self, owner, owner_vehicle):
        h = {"Authorization": f"Bearer {owner['token']}"}
        ws = "2026-06-16"
        r = requests.post(f"{API}/weekly-walkarounds",
                          json={"vehicle_reg": owner_vehicle["registration"], "week_start": ws, "driver_name": "PDF"},
                          headers=h)
        wid = r.json()["id"]
        rp = requests.get(f"{API}/weekly-walkarounds/{wid}/sheet", headers=h)
        assert rp.status_code == 200
        assert rp.content.startswith(b"%PDF")
        requests.delete(f"{API}/weekly-walkarounds/{wid}", headers=h)


# ---------- VOR ----------
class TestVOR:
    def test_set_and_clear_vor(self, owner):
        h = {"Authorization": f"Bearer {owner['token']}"}
        reg = f"VR{_run.upper()[:4]}"
        r = requests.post(f"{API}/vehicles", json={"registration": reg, "make": "M"}, headers=h)
        assert r.status_code == 200, r.text
        vid = r.json()["id"]
        try:
            off = date.today().isoformat()
            back = (date.today() + timedelta(days=14)).isoformat()
            rs = requests.post(f"{API}/vehicles/{vid}/vor",
                               json={"reason": "Engine failure", "off_date": off, "expected_return": back},
                               headers=h)
            assert rs.status_code == 200, rs.text
            rv = requests.get(f"{API}/vehicles", headers=h)
            veh = next(v for v in rv.json() if v["id"] == vid)
            assert veh["vor"] is True
            assert veh["vor_reason"] == "Engine failure"
            rc = requests.get(f"{API}/calendar", headers=h)
            assert rc.status_code == 200
            # calendar API strips 'ref'; match by title prefix
            events = [e for e in rc.json() if isinstance(e.get("title"), str) and e["title"].startswith("VOR — ") and reg in e["title"]]
            assert len(events) >= 2, f"Expected >=2 VOR calendar events, got {len(events)}"
            rc2 = requests.post(f"{API}/vehicles/{vid}/vor/clear", headers=h)
            assert rc2.status_code == 200
            rv2 = requests.get(f"{API}/vehicles", headers=h)
            veh2 = next(v for v in rv2.json() if v["id"] == vid)
            assert veh2["vor"] is False
            rc3 = requests.get(f"{API}/calendar", headers=h)
            events2 = [e for e in rc3.json() if isinstance(e.get("title"), str) and e["title"].startswith("VOR — ") and reg in e["title"]]
            assert events2 == [], f"VOR events not cleared: {events2}"
        finally:
            requests.delete(f"{API}/vehicles/{vid}", headers=h)


# ---------- Repairs CRUD ----------
class TestRepairs:
    def test_repairs_crud(self, owner, owner_vehicle):
        h = {"Authorization": f"Bearer {owner['token']}"}
        payload = {
            "vehicle_reg": owner_vehicle["registration"],
            "repair_date": date.today().isoformat(),
            "category": "Accident damage",
            "description": "Front bumper replaced",
            "provider": "ACME Repairs",
            "cost": 1250.5,
        }
        r = requests.post(f"{API}/repairs", json=payload, headers=h)
        assert r.status_code == 200, r.text
        rec = r.json()
        assert rec["category"] == "Accident damage"
        rid = rec["id"]

        rl = requests.get(f"{API}/repairs", headers=h)
        assert rl.status_code == 200
        assert any(x["id"] == rid for x in rl.json())

        p2 = {**payload, "cost": 999.99, "description": "Updated"}
        ru = requests.put(f"{API}/repairs/{rid}", json=p2, headers=h)
        assert ru.status_code == 200
        got = next(x for x in requests.get(f"{API}/repairs", headers=h).json() if x["id"] == rid)
        assert got["cost"] == 999.99
        assert got["description"] == "Updated"

        rd = requests.delete(f"{API}/repairs/{rid}", headers=h)
        assert rd.status_code == 200
        assert not any(x["id"] == rid for x in requests.get(f"{API}/repairs", headers=h).json())


# ---------- Cleanup ----------
def teardown_module(module):
    try:
        db = _mongo()
        db.users.delete_many({"email": {"$regex": f"_{_run}@"}})
        db.invitations.delete_many({"email": {"$regex": f"_{_run}@"}})
    except Exception as e:
        print(f"cleanup warn: {e}")

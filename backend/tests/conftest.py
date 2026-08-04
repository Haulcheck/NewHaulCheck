"""Shared test bootstrap.

The suites here are integration tests: they drive a *live* backend over HTTP
rather than importing `server`. Each one resolves the API base URL at module
import time from `REACT_APP_BACKEND_URL`, and eleven of them fall back to
reading the hard-coded Linux path `/app/frontend/.env` -- the layout of the
Emergent container. That path does not exist on a developer machine, so the
fallback raised and collection failed before a single test ran.

pytest imports conftest before any test module, and every one of those
fallbacks checks the environment variable *first*. So populating the variable
here repairs all of them at once, without touching the test files themselves.

Resolution order:
  1. REACT_APP_BACKEND_URL already exported  -> respected, never overridden,
     so CI and the hosted preview keep working unchanged.
  2. frontend/.env, located relative to this file rather than assumed at /app.
  3. http://localhost:8000, the default in frontend/.env.example.
"""
import os
import pytest
from pathlib import Path

DEFAULT_BASE_URL = "http://localhost:8000"
# tests/ -> backend/ -> Haulcheck-main/ -> frontend/.env
FRONTEND_ENV = Path(__file__).resolve().parents[2] / "frontend" / ".env"


def _from_frontend_env() -> str | None:
    try:
        for line in FRONTEND_ENV.read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'").rstrip("/")
                if value:
                    return value
    except OSError:
        pass
    return None


def _resolve_base_url() -> str:
    return (
        (os.environ.get("REACT_APP_BACKEND_URL") or "").strip().rstrip("/")
        or _from_frontend_env()
        or DEFAULT_BASE_URL
    )


# Set before test modules are imported -- this is what makes the /app fallbacks
# unreachable. Exported so tests spawned by xdist workers inherit it too.
os.environ["REACT_APP_BACKEND_URL"] = _resolve_base_url()


# Tests that read source rather than driving the API. They are the build's
# safety net -- tenancy scoping, vendor coupling, third-party scripts, the
# dependency manifest -- so they must be runnable in CI and in a container
# build, where no backend is listening. Gating them on a live API meant the
# guards could only run on a machine that already had the whole stack up.
OFFLINE_TEST_MODULES = {
    "test_tenancy_guard",
    "test_provider_decoupling",
    "test_no_third_party_frontend",
    "test_requirements",
    "test_render_blueprint",
    "test_sigv4",
    # Its round-trip class is skipped unless the S3_* variables are set, so
    # without a bucket configured the module drives nothing over HTTP.
    "test_s3_storage",
}


def _selection_is_offline_only(config) -> bool:
    """True when every path given on the command line is an offline module."""
    args = [a for a in config.args if not a.startswith("-")]
    if not args:
        return False
    for arg in args:
        stem = Path(arg.split("::")[0]).stem
        if stem not in OFFLINE_TEST_MODULES:
            return False
    return True


def pytest_configure(config):
    """Refuse to run against anything that is not this backend.

    Ports get squatted. A different application answering on :8000 returns 200
    to a naive reachability probe, and the whole suite then fails in confusing
    ways against software that has nothing to do with HaulCheck. Checking for a
    known HaulCheck response turns that into one clear message.

    Skipped when the run selects only source-reading tests, which need no
    backend at all.
    """
    import json
    import urllib.error
    import urllib.request

    if _selection_is_offline_only(config):
        return

    base = os.environ["REACT_APP_BACKEND_URL"]
    url = f"{base}/api/auth/me"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            body, status = r.read(400), r.status
    except urllib.error.HTTPError as e:          # 401 is the healthy answer
        body, status = e.read(400), e.code
    except Exception as e:
        raise pytest.UsageError(
            f"Cannot reach the HaulCheck API at {base} ({e}).\n"
            f"Start it with:  cd backend && bash run-dev.sh"
        )

    try:
        detail = json.loads(body).get("detail", "")
    except Exception:
        detail = ""
    if status != 401 or "auth" not in detail.lower():
        raise pytest.UsageError(
            f"{url} answered {status} with an unexpected body -- this does not look "
            f"like the HaulCheck API.\n"
            f"Another application may be listening on that port. Expected 401 "
            f'{{"detail": "Not authenticated"}}, got: {body[:120]!r}'
        )

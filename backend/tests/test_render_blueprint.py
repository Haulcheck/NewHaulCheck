"""Static guard: the blueprint is the only deployment configuration.

render.yaml is what Render reads to build both halves of the app. A second
config file that Render ignores -- vercel.json was one -- is worse than no
config at all, because it looks authoritative and silently is not.
"""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent.parent
BLUEPRINT = REPO / "render.yaml"


def _services():
    spec = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))
    return {s["name"]: s for s in spec["services"]}


def test_blueprint_declares_both_halves():
    names = set(_services())
    assert names == {"haulcheck-api", "haulcheck-web"}, (
        "The blueprint must declare exactly the API and the web app. Found: "
        + ", ".join(sorted(names))
    )


def test_web_service_builds_the_cra_bundle():
    web = _services()["haulcheck-web"]
    assert web["runtime"] == "static"
    assert web["rootDir"] == "frontend"
    assert web["staticPublishPath"] == "./build"
    assert "yarn build" in web["buildCommand"]


def test_web_service_rewrites_deep_links_to_the_spa():
    routes = _services()["haulcheck-web"]["routes"]
    rewrite = [r for r in routes if r["type"] == "rewrite"]
    assert rewrite, (
        "Without a catch-all rewrite, a React Router deep link such as "
        "/maintenance returns 404 on a hard refresh."
    )
    assert rewrite[-1]["source"] == "/*"
    assert rewrite[-1]["destination"] == "/index.html"


def test_backend_url_is_a_build_time_variable():
    envs = {e["key"]: e for e in _services()["haulcheck-web"]["envVars"]}
    assert "REACT_APP_BACKEND_URL" in envs, (
        "CRA compiles this into the bundle at build time; it cannot be set later."
    )
    assert envs["REACT_APP_BACKEND_URL"].get("sync") is False


def test_no_competing_deployment_config():
    assert not (REPO / "frontend" / "vercel.json").exists(), (
        "vercel.json is not read by Render. Its contents belong in render.yaml."
    )


def test_storage_provider_is_not_pinned_in_the_blueprint():
    """A blueprint sync must not be able to break a working deployment.

    STORAGE_PROVIDER was `value: s3` while the four S3_* values it needs were
    sync: false. A first deploy therefore built, booted, and then failed its
    health check on a missing key. Setting it to `null` in the dashboard fixed
    that -- until the next blueprint sync overwrote it back to `s3`.
    """
    envs = {e["key"]: e for e in _services()["haulcheck-api"]["envVars"]}
    sp = envs["STORAGE_PROVIDER"]
    assert "value" not in sp, (
        "STORAGE_PROVIDER must not carry a hardcoded value: a re-sync would "
        "overwrite whatever the dashboard is set to and break production."
    )
    assert sp.get("sync") is False


def test_s3_settings_left_to_the_dashboard():
    """The credentials and the switch that needs them travel together."""
    envs = {e["key"]: e for e in _services()["haulcheck-api"]["envVars"]}
    for key in ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        assert envs[key].get("sync") is False, f"{key} must not be committed"


GUIDE = REPO / "DEPLOYMENT.md"


def test_guide_does_not_send_the_operator_to_vercel():
    """The guide is followed literally by someone who is not a developer.

    A leftover Vercel step does not read as stale documentation to them -- it
    reads as a required account they must go and create.
    """
    lines = [
        f"  line {n}: {line.strip()}"
        for n, line in enumerate(GUIDE.read_text(encoding="utf-8").splitlines(), 1)
        if "vercel" in line.lower()
    ]
    assert not lines, "DEPLOYMENT.md still references Vercel:\n" + "\n".join(lines)


def test_guide_names_the_deploy_repository():
    text = GUIDE.read_text(encoding="utf-8")
    assert "Furqan-10/NewHaulCheck" in text
    assert "Furqan-10/OUR-Haul" not in text, (
        "OUR-Haul is not the deploy repository. Render builds from NewHaulCheck."
    )

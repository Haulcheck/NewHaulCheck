"""Static guard: the browser bundle talks only to hosts the client owns.

Anything loaded here runs inside a compliance product that displays driver
licence numbers, CPC status and tachograph records. A third-party script is not
just a dependency -- it is an unaudited party with read access to that screen,
and under GDPR it is the operator's disclosure to justify.

Two things this was written to stop coming back:

* `assets.emergent.sh/scripts/emergent-main.js`, the platform loader.
* A PostHog snippet with `session_recording` enabled, keyed to an analytics
  project belonging to whoever generated the app -- so replays of those screens
  were being sent somewhere the deployment does not control.

It reads source rather than behaviour: detecting this at runtime would mean
loading the page against a live analytics account to discover it was leaking.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
FRONTEND = REPO / "frontend"

# The platform being migrated away from, plus the analytics vendor whose key was
# baked into index.html.
FORBIDDEN = re.compile(
    r"emergentagent\.com|emergent\.sh|emergent\.host|emergentbase|posthog",
    re.IGNORECASE,
)

SCANNED_DIRS = (FRONTEND / "public", FRONTEND / "src")
SCANNED_SUFFIXES = {".html", ".js", ".jsx", ".ts", ".tsx", ".json", ".txt", ".xml", ".css"}


def _scanned_files():
    for root in SCANNED_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in SCANNED_SUFFIXES:
                yield path
    for name in ("package.json", "craco.config.js"):
        candidate = FRONTEND / name
        if candidate.exists():
            yield candidate


def test_no_third_party_host_in_the_frontend():
    violations = []
    for path in _scanned_files():
        source = path.read_text(encoding="utf-8", errors="replace")
        for match in FORBIDDEN.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            violations.append(
                f"  {path.relative_to(FRONTEND).as_posix()}:{line}  "
                f"references `{match.group(0)}`")

    assert not violations, (
        "The frontend references a host the client does not control.\n\n"
        "Scripts loaded in the browser can read every field on the page, which\n"
        "here includes driver licence and tachograph data. Remove the reference,\n"
        "or self-host the asset.\n\n" + "\n".join(sorted(violations))
    )


PRODUCTION_ORIGIN = "https://haulcheck.co.uk"


def test_production_domain_is_filled_in():
    """The canonical, sitemap and robots entries name the real domain.

    These three shipped as commented placeholders because a canonical pointing
    at the wrong host tells search engines the app lives somewhere else. That
    reasoning cuts both ways: once the domain exists, leaving them commented
    means the app never claims its own address.
    """
    missing = []
    for name in ("index.html", "robots.txt", "sitemap.xml"):
        source = (FRONTEND / "public" / name).read_text(encoding="utf-8")
        if PRODUCTION_ORIGIN not in source:
            missing.append(name)
        if "app.example.com" in source:
            missing.append(f"{name} (still has the example.com placeholder)")

    assert not missing, (
        "Production domain not set in: " + ", ".join(missing)
    )


def test_emergent_platform_files_are_gone():
    leftovers = [p.name for p in (REPO / ".emergent", REPO / ".gitconfig") if p.exists()]
    assert not leftovers, (
        "Emergent platform files remain: " + ", ".join(leftovers) + "\n"
        ".gitconfig sets the commit author to github@emergent.sh; .emergent/ pins "
        "a platform image that this deployment does not run on."
    )

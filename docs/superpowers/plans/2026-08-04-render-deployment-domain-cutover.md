# Render Deployment and haulcheck.co.uk Cutover — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a push to `main` on `github.com/Furqan-10/NewHaulCheck` reach `haulcheck.co.uk`, served entirely from Render with no Emergent involvement.

**Architecture:** Two Render services from one `render.yaml` blueprint — a Docker web service running FastAPI at `api.haulcheck.co.uk`, and a static site serving the CRA build at `haulcheck.co.uk`. Backing services are MongoDB Atlas M0, Cloudflare R2 and Resend. DNS stays at Fasthosts and is repointed at Render.

**Tech Stack:** FastAPI + uvicorn (Docker), React 19 / CRA via CRACO, MongoDB Atlas, Cloudflare R2 (S3-compatible), Resend, Render Blueprints, cron-job.org.

**Spec:** [2026-08-04-render-deployment-domain-cutover-design.md](../specs/2026-08-04-render-deployment-domain-cutover-design.md)

## Global Constraints

- Work in the `ourhaul-deploy` worktree at `D:/work/ourhaul-deploy`. The repository root is the app root — `backend/` and `frontend/` are top-level.
- Production domain is `haulcheck.co.uk`. API is `api.haulcheck.co.uk`. No other hostnames.
- `REACT_APP_BACKEND_URL` carries **no trailing slash and no `/api` suffix** — the client appends `/api` itself. A trailing `/api` produces `/api/api/...` and every call 404s.
- `REACT_APP_BACKEND_URL` is compiled into the bundle at build time. Changing it requires a rebuild, not a restart.
- `CORS_ORIGINS` is an explicit allow-list. In production the API refuses to start on an unset or `*` value. Scheme included, no trailing slash.
- Render is IPv4-only. No `AAAA` records for any Render-backed hostname.
- Render apex A record is `216.24.57.1`. Subdomains use a CNAME to the service's `onrender.com` hostname.
- `AI_PROVIDER=null` and `GOOGLE_CLIENT_ID` unset. Both stay that way in this plan.
- Backend tests are integration tests against a live API. `pytest.ini` pins `addopts = -n 2 --dist loadscope`; **do not modify `addopts`**. Run serially with `pytest -n 0` (never `-p no:xdist`).
- Commit after each task. Do not force-push anything except the one explicitly authorised push in Task 4.

## Task Sequence

Tasks 1–4 are repository work and can run back to back. Tasks 5–11 are operational and touch live infrastructure — Task 9 changes what the public sees.

---

### Task 1: Move the frontend build config from Vercel into `render.yaml`

**Files:**
- Create: `backend/tests/test_render_blueprint.py`
- Modify: `render.yaml`
- Modify: `backend/requirements-dev.txt`
- Delete: `frontend/vercel.json`

**Note on `buildFilter`:** Render's docs returned three different schemas for it
(`paths`/`ignoredPaths`, `include`/`exclude` nested, `include`/`exclude` as a
list). It is only an optimisation — it stops a backend-only commit rebuilding the
frontend — and a wrong key risks failing blueprint validation and blocking the
whole deploy. It is deliberately omitted. Add it later from the dashboard, where
the schema is enforced interactively, if the redundant rebuilds prove annoying.

**Interfaces:**
- Consumes: nothing.
- Produces: a Render blueprint declaring two services, `haulcheck-api` (existing) and `haulcheck-web` (new). Task 6 deploys from it. Task 8 reads the static site's `onrender.com` hostname from it.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_render_blueprint.py`:

```python
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
```

- [ ] **Step 2: Declare the test's dependency**

The test imports `yaml`. It happens to be importable in the current environment as
a transitive dependency, but it is named in neither `requirements.txt` nor
`requirements-dev.txt` — so the test would fail on a fresh clone and in CI. Add to
`backend/requirements-dev.txt`:

```
pyyaml==6.0.3
```

Dev, not runtime: nothing the server does at run time parses YAML. Adding it to
`requirements.txt` would put it in the production image for no reason, which is
exactly the habit that made the original `requirements.txt` uninstallable.

- [ ] **Step 3: Run the test and confirm it fails**

```bash
cd /d/work/ourhaul-deploy/backend && python -m pytest tests/test_render_blueprint.py -n 0 -q
```

Expected: failures on `test_blueprint_declares_both_halves` (only `haulcheck-api` present) and `test_no_competing_deployment_config`.

- [ ] **Step 4: Append the static site to `render.yaml`**

Add below the existing `haulcheck-api` block, at the same indentation:

```yaml
  # The web app. A static bundle on Render's CDN rather than a second container:
  # it costs nothing, and it keeps loading while the free-tier API is asleep, so
  # a visitor sees the app shell instead of a blank fifty-second wait.
  - type: web
    name: haulcheck-web
    runtime: static
    rootDir: frontend
    buildCommand: yarn install --frozen-lockfile && GENERATE_SOURCEMAP=false yarn build
    staticPublishPath: ./build
    routes:
      # React Router owns the paths. Without this, opening /maintenance directly
      # -- or refreshing it -- asks Render for a file that was never built.
      - type: rewrite
        source: /*
        destination: /index.html
    headers:
      - path: /*
        name: X-Content-Type-Options
        value: nosniff
      - path: /*
        name: X-Frame-Options
        value: DENY
      - path: /*
        name: Referrer-Policy
        value: strict-origin-when-cross-origin
      # The driver app photographs defects and scans onboarding QR codes, so the
      # camera is needed. Nothing else is.
      - path: /*
        name: Permissions-Policy
        value: camera=(self), microphone=(), geolocation=()
      # CRA fingerprints these filenames, so a changed file is a changed URL and
      # this can be cached indefinitely.
      - path: /static/*
        name: Cache-Control
        value: public, max-age=31536000, immutable
    envVars:
      # Compiled into the bundle at build time -- changing it needs a rebuild,
      # not a restart. Set to https://api.haulcheck.co.uk once DNS is live.
      - key: REACT_APP_BACKEND_URL
        sync: false
```

- [ ] **Step 5: Delete the Vercel config**

```bash
cd /d/work/ourhaul-deploy && git rm frontend/vercel.json
```

- [ ] **Step 6: Run the tests and confirm they pass**

```bash
cd /d/work/ourhaul-deploy/backend && python -m pytest tests/test_render_blueprint.py -n 0 -q
```

Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
cd /d/work/ourhaul-deploy
git add render.yaml backend/tests/test_render_blueprint.py backend/requirements-dev.txt
git commit -m "Serve the web app from Render instead of Vercel

Render static sites provide the CDN, auto-deploy and rewrite rules Vercel was
chosen for, so the second vendor bought nothing. Everything the vercel.json
carried -- SPA rewrite, security headers, immutable caching on fingerprinted
assets -- moves into the blueprint, and the file goes, because a config Render
never reads is worse than none.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Fill in the production domain

**Files:**
- Modify: `frontend/public/index.html:19-22`
- Modify: `frontend/public/robots.txt` (last two lines)
- Modify: `frontend/public/sitemap.xml`
- Modify: `backend/tests/test_no_third_party_frontend.py` (append one test)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing later tasks read. Independent of Tasks 1, 3, 4.

Context: the 2026-07-28 work removed the Emergent URLs rather than replacing them, leaving three commented-out `https://app.example.com` placeholders that say to complete them when the domain exists. It exists.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_no_third_party_frontend.py`:

```python
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
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /d/work/ourhaul-deploy/backend && python -m pytest tests/test_no_third_party_frontend.py::test_production_domain_is_filled_in -n 0 -q
```

Expected: FAIL listing all three files.

- [ ] **Step 3: Set the canonical URL**

In `frontend/public/index.html`, replace the four-line comment block at lines 19–22:

```html
        <!-- Set once the production domain is live. A canonical pointing at the
             wrong host tells search engines the app lives somewhere else, so it
             is better absent than stale:
             <link rel="canonical" href="https://app.example.com/" /> -->
```

with:

```html
        <link rel="canonical" href="https://haulcheck.co.uk/" />
```

- [ ] **Step 4: Set the sitemap reference in `robots.txt`**

Replace the last two lines:

```
# Uncomment once the production domain is live:
# Sitemap: https://app.example.com/sitemap.xml
```

with:

```
Sitemap: https://haulcheck.co.uk/sitemap.xml
```

- [ ] **Step 5: Write the sitemap**

Replace the entire contents of `frontend/public/sitemap.xml` with:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://haulcheck.co.uk/</loc>
         <changefreq>weekly</changefreq><priority>1.0</priority></url>
    <url><loc>https://haulcheck.co.uk/login</loc>
         <changefreq>monthly</changefreq><priority>0.8</priority></url>
</urlset>
```

Only these two. Every other route is behind authentication and `Disallow`ed in `robots.txt`; listing them would invite crawlers to paths that only return a login redirect.

- [ ] **Step 6: Run the full frontend guard suite**

```bash
cd /d/work/ourhaul-deploy/backend && python -m pytest tests/test_no_third_party_frontend.py -n 0 -q
```

Expected: 3 passed. The pre-existing Emergent/PostHog guard must still pass — it proves this change introduced no third-party host.

- [ ] **Step 7: Commit**

```bash
cd /d/work/ourhaul-deploy
git add frontend/public/index.html frontend/public/robots.txt frontend/public/sitemap.xml backend/tests/test_no_third_party_frontend.py
git commit -m "Claim haulcheck.co.uk in the canonical, sitemap and robots

These shipped commented out because a canonical pointing at the wrong host is
worse than none. The domain exists now, so the same reasoning says to fill them
in: until it is set, the app never claims its own address.

Only / and /login are listed. Everything else is behind authentication and
already disallowed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Rewrite `DEPLOYMENT.md` for a Render-only stack

**Files:**
- Modify: `DEPLOYMENT.md` — architecture diagram (~line 17), service table (~line 30), account list (~line 50), repo URL (~line 58), step 4 (~line 138), step 5 (~line 184), step 6 (~line 209), "When the domain arrives" (~line 330), cost table (~line 388)
- Modify: `backend/tests/test_render_blueprint.py` (append one test)

**Interfaces:**
- Consumes: the `haulcheck-web` service name from Task 1.
- Produces: the runbook Tasks 5–11 are executed from.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_render_blueprint.py`:

```python
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
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /d/work/ourhaul-deploy/backend && python -m pytest tests/test_render_blueprint.py -n 0 -q
```

Expected: both new tests FAIL — Vercel appears in the diagram, table, account list, step 5, step 6 and cost table; and the repo is named `OUR-Haul`.

- [ ] **Step 3: Update the architecture diagram and service table**

Replace the diagram under `## What you are building` with:

```
Browser
   │
   ├──► Render  ·  the web app  ·  haulcheck.co.uk
   │       └─ REACT_APP_BACKEND_URL ──┐
   │                                  ▼
   └──────────────────────► Render  ·  the API  ·  api.haulcheck.co.uk
                                  ├──► MongoDB Atlas   the database
                                  ├──► Cloudflare R2   uploaded photos and PDFs
                                  └──► Resend          reminder and alert email

   cron-job.org ──► POST /api/tasks/run-reminders  ·  daily 07:00 UTC
```

Change "Five services" to "Four services". In the table, delete the Vercel row and add:

```
| **Render** — the web app | Free, unlimited static sites | Included in the same plan |
```

- [ ] **Step 4: Update the account list and repository URL**

Under `## Before you start`, change "Create these five accounts" to "Create these four accounts" and delete item 5 (Vercel). Change the repository line to:

```
You also need the code on GitHub. It already is:
`https://github.com/Furqan-10/NewHaulCheck`.
```

In step 4, change the blueprint connection line to name `Furqan-10/NewHaulCheck`, and note that the blueprint now creates **two** services, not one.

- [ ] **Step 5: Replace step 5 entirely**

```markdown
## Step 5 — The web app

The blueprint in step 4 already created this alongside the API — Render reads
both services from the same `render.yaml`. This step only fills in its one
variable.

1. Render dashboard → `haulcheck-web` → **Environment**.
2. Add:

   | Name | Value |
   |---|---|
   | `REACT_APP_BACKEND_URL` | `https://haulcheck-api.onrender.com` |

   **No trailing slash, and no `/api`** — the app appends that itself. A
   trailing `/api` produces requests to `/api/api/...` and every call 404s.

3. **Manual Deploy** → **Deploy latest commit**.

**Write down:** the web app URL, e.g. `https://haulcheck-web.onrender.com`

> This value is compiled into the JavaScript at build time, not read when the
> page loads. Changing it later means redeploying the web app, not just editing
> the variable. You will change it once more in step 9, when the domain is live.
```

- [ ] **Step 6: Update step 6 to name the Render web URL**

In step 6, replace every `https://haulcheck.vercel.app` with `https://haulcheck-web.onrender.com`, and "your Vercel URL" with "your web app URL". Replace the closing blockquote about Vercel previews with:

```markdown
> **Render preview environments will not work** against this API, and that is
> deliberate. Each preview gets its own URL, and `CORS_ORIGINS` is also what
> validates OAuth redirect URIs. Widening it to a wildcard would let any
> subdomain call a credentialed API. Test against the real URL.
```

- [ ] **Step 7: Rewrite "When the domain arrives" as the live cutover**

Replace that section's heading with `## Step 9 — The domain` and its numbered list with:

```markdown
The domain is `haulcheck.co.uk`, registered at Fasthosts. DNS is managed there
too — the nameservers are `ns1.livedns.co.uk`, `ns2` and `ns3`.

**A day before:** Fasthosts control panel → DNS → lower the TTL on the existing
records to 300 seconds. Propagation is governed by the *old* TTL, so this has to
be done ahead of the change, not during it.

**Record the current values before changing anything** — these are the rollback:

| Record | Type | Current value |
|---|---|---|
| `@` | A | `162.159.142.117` |
| `@` | A | `172.66.2.113` |
| `www` | CNAME | `haulcheck.co.uk` |

1. **Render** → `haulcheck-api` → **Settings** → **Custom Domain** → add
   `api.haulcheck.co.uk`.
2. **Fasthosts** → add `api` as a CNAME to `haulcheck-api.onrender.com`. Wait
   for Render to show the domain as verified, then confirm
   `https://api.haulcheck.co.uk/api/health` answers.
3. **Render** → `haulcheck-web` → **Custom Domain** → add both
   `haulcheck.co.uk` and `www.haulcheck.co.uk`.
4. **Fasthosts** — now the visible change, both together:
   - Delete **both** apex `A` records above. Add one `A` record on `@` pointing
     to `216.24.57.1`.
   - Change `www` from a CNAME to the apex into a CNAME to
     `haulcheck-web.onrender.com`.
   - Delete any `AAAA` records. Render is IPv4-only and a stray `AAAA` causes
     intermittent failures that look like a broken site to some visitors and a
     working one to you.
5. TLS is automatic. Render issues and renews the certificates and redirects
   HTTP to HTTPS; no action and no cost.

> **Why `api` first.** If the apex moved first, the web app would be live at
> haulcheck.co.uk while still compiled against an API the browser cannot reach —
> a site that loads and then fails at login. Proving `api` first means the only
> visible change is the last one.
```

- [ ] **Step 8: Update the cost table**

In `## Going paid`, delete the `Vercel Pro | $20/mo | Required for commercial use` row. Render static sites carry no such restriction, which is worth stating — add below the table:

```markdown
> Dropping Vercel removed a £20/month obligation, not just a step: Vercel's free
> tier forbids commercial use, and this app is a commercial product. Render's
> static sites have no equivalent clause.
```

- [ ] **Step 9: Run the tests and confirm they pass**

```bash
cd /d/work/ourhaul-deploy/backend && python -m pytest tests/test_render_blueprint.py -n 0 -q
```

Expected: 7 passed.

- [ ] **Step 10: Commit**

```bash
cd /d/work/ourhaul-deploy
git add DEPLOYMENT.md backend/tests/test_render_blueprint.py
git commit -m "Point the deployment guide at Render and the real domain

The guide is followed literally by someone who is not a developer, so a
leftover Vercel step does not read to them as stale documentation -- it reads
as an account they must go and create.

Folds 'when the domain arrives' into the main flow with the actual Fasthosts
records, and orders the cutover so api is proven before the apex moves. The
reverse leaves the site loading but failing at login.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Make NewHaulCheck the deploy repository

**Files:**
- Modify: `DEPLOYMENT.md` (no further edits if Task 3 done — verify only)
- Modify: `.git/config` (adds a remote)

**Interfaces:**
- Consumes: the completed working tree from Tasks 1–3.
- Produces: `github.com/Furqan-10/NewHaulCheck` @ `main` at the current `ourhaul-deploy` tip. Task 6 connects Render to it.

**Authorisation:** Furqan has approved the force-push. It overwrites two import commits (`2d351e3`, `dce0f1e`) that nothing is built on.

- [ ] **Step 1: Confirm their correction is already carried**

`2d351e3` ("Repo Link Correction") changed the repo URL in `DEPLOYMENT.md` from `Furqan-10/OUR-Haul` to `Furqan-10/NewHaulCheck` in three places. Task 3 step 4 made the same change, and Task 3's guard test enforces it. Verify — force-pushing without this silently reinstates a stale repo URL in the guide the client follows:

```bash
cd /d/work/ourhaul-deploy && grep -c "Furqan-10/NewHaulCheck" DEPLOYMENT.md && grep -c "OUR-Haul" DEPLOYMENT.md
```

Expected: a non-zero count, then `0`. If the second is not zero, fix it before continuing.

- [ ] **Step 2: Add the remote**

```bash
cd /d/work/ourhaul-deploy
git remote add newhaul https://github.com/Furqan-10/NewHaulCheck.git
git fetch newhaul
```

- [ ] **Step 3: Record what is being overwritten**

```bash
git log --oneline newhaul/main
```

Expected: exactly `2d351e3` and `dce0f1e`. **If there are more commits than this, stop** — someone has pushed since this plan was written, and the force-push would destroy their work. Reconcile before proceeding.

- [ ] **Step 4: Confirm the whole suite passes before publishing**

```bash
cd /d/work/ourhaul-deploy/backend && python -m pytest tests/test_render_blueprint.py tests/test_no_third_party_frontend.py tests/test_requirements.py tests/test_provider_decoupling.py -n 0 -q
```

Expected: all pass. These are the offline guards; the integration suite needs a live API and runs in Task 10.

- [ ] **Step 5: Force-push**

```bash
git push --force-with-lease=main:2d351e39b2887f04129788db87106e147f81d938 newhaul ourhaul-deploy:main
```

`--force-with-lease` pinned to the known SHA makes this refuse rather than overwrite if `main` moved after step 3.

- [ ] **Step 6: Verify**

```bash
git fetch newhaul && git rev-parse ourhaul-deploy newhaul/main
```

Expected: two identical SHAs, and `git log --oneline newhaul/main | wc -l` reports 29 or more.

- [ ] **Step 7: Set the branch to track**

```bash
git branch --set-upstream-to=newhaul/main ourhaul-deploy
```

From here `git push` publishes, and Render rebuilds. That is the mechanism this whole plan exists to create.

---

### Task 5: Provision the backing services

**Operator task — needs account signups. No code.**

Follow `DEPLOYMENT.md` steps 1–3 exactly. Use the **client's** email; these hold their data and they should own them.

- [ ] **Step 1: MongoDB Atlas** — free M0 cluster, region Frankfurt (`eu-central-1`). Create a database user. Network access `0.0.0.0/0` (Render's free tier has no static egress IP, so an allow-list is not available). Collect the `mongodb+srv://` connection string and insert the password into it.
- [ ] **Step 2: Cloudflare R2** — create a bucket. Create an API token scoped to it. Collect account ID, bucket name, access key, secret key. Set bucket CORS to allow the production origin.
- [ ] **Step 3: Resend** — create an API key, verify a sender address.
- [ ] **Step 4: Record all values** in one scratch file. Step 4 of the guide needs them simultaneously.

**Verification:** you should have eight values — `MONGO_URL`, `S3_BUCKET`, `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `RESEND_API_KEY`, `SENDER_EMAIL`, and the R2 account ID.

---

### Task 6: Deploy both services from the blueprint

**Operator task, Render dashboard.**

- [ ] **Step 1:** Render → **New** → **Blueprint** → connect `Furqan-10/NewHaulCheck`. It reads `render.yaml` and proposes **two** services: `haulcheck-api` and `haulcheck-web`.
- [ ] **Step 2:** Fill in the `sync: false` variables on `haulcheck-api` from Task 5. Leave `CORS_ORIGINS` as the `onrender.com` web URL for now; it changes in Task 9.
- [ ] **Step 3:** Set `REACT_APP_BACKEND_URL` on `haulcheck-web` to `https://haulcheck-api.onrender.com`.
- [ ] **Step 4:** Deploy. The API build is a Docker build and takes several minutes on the free tier.
- [ ] **Step 5:** Confirm `https://haulcheck-api.onrender.com/api/health` returns healthy, and that it reports `storage: s3` and `ai: null`.

**If the health check fails:** read the Render logs. The two most likely causes are a `mongodb+srv://` URL failing because `dnspython` is missing (it is in `requirements.txt` — confirm the build picked it up), and the app refusing to start because `CORS_ORIGINS` is unset in `ENVIRONMENT=production`.

---

### Task 7: Smoke test before touching DNS

**Operator task.** Against the `onrender.com` URLs, not the domain.

- [ ] Register an account and log in — proves Atlas connectivity and `JWT_SECRET`.
- [ ] Upload a defect photo — **proves R2 and the SigV4 implementation against a real bucket.** This is the highest-value check; SigV4 was written against test vectors and this is its first contact with live storage.
- [ ] Generate a PDF audit pack — proves reportlab and pypdf merging inside the container.
- [ ] Open a deep link such as `/maintenance` directly and hard-refresh it — proves the SPA rewrite from Task 1.
- [ ] Open the browser network tab and confirm no request goes to any Emergent host.

Do not proceed to Task 8 until all five pass. After DNS moves, a failure here is a failure on the live domain.

---

### Task 8: Repoint DNS at Fasthosts

**Operator task.** Follow `DEPLOYMENT.md` step 9 as rewritten in Task 3. In summary:

- [ ] **Step 1:** A day ahead — lower TTL on the existing records to 300s.
- [ ] **Step 2:** Record the current values (`162.159.142.117`, `172.66.2.113`, `www` → apex). This is the rollback.
- [ ] **Step 3:** Add `api.haulcheck.co.uk` in Render, then the CNAME at Fasthosts. Confirm `https://api.haulcheck.co.uk/api/health` answers before going further.
- [ ] **Step 4:** Add `haulcheck.co.uk` and `www.haulcheck.co.uk` in Render.
- [ ] **Step 5:** At Fasthosts, together: replace both apex `A` records with one pointing to `216.24.57.1`; repoint `www` to `haulcheck-web.onrender.com`; delete any `AAAA` records.
- [ ] **Step 6:** Confirm TLS is issued (Render does this automatically) and that `http://` redirects to `https://`.

**Rollback:** restore the two A records and the `www` CNAME from step 2. With a 300s TTL this takes effect in minutes.

---

### Task 9: Rebuild against the real domain

**Operator task. The site is live but still compiled against `onrender.com` until this is done.**

- [ ] **Step 1:** Render → `haulcheck-api` → Environment → set `CORS_ORIGINS` to `https://haulcheck.co.uk`. Save; it redeploys.
- [ ] **Step 2:** Render → `haulcheck-web` → Environment → set `REACT_APP_BACKEND_URL` to `https://api.haulcheck.co.uk`.
- [ ] **Step 3:** `haulcheck-web` → **Manual Deploy** → **Deploy latest commit**. The variable is compiled in; saving it alone changes nothing.
- [ ] **Step 4:** Hard-refresh `https://haulcheck.co.uk`, log in, and confirm in the network tab that calls go to `api.haulcheck.co.uk`.

---

### Task 10: Reminders and the acceptance suite

- [ ] **Step 1:** Copy `CRON_SECRET` from the Render dashboard (it was generated by `generateValue`).
- [ ] **Step 2:** cron-job.org → new job → `POST https://api.haulcheck.co.uk/api/tasks/run-reminders`, header `Authorization: Bearer <CRON_SECRET>`, daily at 07:00 UTC.
- [ ] **Step 3:** Run it once from the dashboard. Expect a 200 and per-job counts in the response body.
- [ ] **Step 4:** Warm the API first, then run the acceptance suite:

```bash
curl -s https://api.haulcheck.co.uk/api/health > /dev/null
cd /d/work/ourhaul-deploy/backend
REACT_APP_BACKEND_URL=https://api.haulcheck.co.uk python -m pytest -n 0 -q
```

The warm-up matters: the free tier's cold start exceeds most default HTTP timeouts, and without it the first tests fail for the wrong reason.

---

### Task 11: Decommission Emergent

- [ ] **Step 1:** Confirm with the client that `haulcheck.co.uk` behaves correctly for them for at least 24 hours.
- [ ] **Step 2:** Ask the client to stop or delete the Emergent deployment. Leaving it running means two live copies of a compliance product against two separate databases, and nobody will work out quickly which one a given record is in.
- [ ] **Step 3:** Confirm no DNS record still points at `162.159.142.117` or `172.66.2.113`.
- [ ] **Step 4:** Take the first backup, since Atlas M0 has none:

```bash
mongodump --uri="$MONGO_URL" --out=backup-$(date +%F)
```

- [ ] **Step 5:** Update `README.md` to state that production is Render, built from `Furqan-10/NewHaulCheck`, and commit.

---

## Where this can go wrong

| Symptom | Cause |
|---|---|
| Every API call 404s with `/api/api/...` | `REACT_APP_BACKEND_URL` has a trailing `/api`. Remove it and **rebuild** |
| Login works, then all calls fail CORS | `CORS_ORIGINS` does not exactly match the origin — check scheme and trailing slash |
| Changing an env var had no effect on the frontend | It is compiled in. Manual Deploy required |
| Site works for you, intermittently fails for others | A stray `AAAA` record. Render is IPv4-only |
| Deep links 404 on refresh, root works | The rewrite in Task 1 is missing or not last |
| First request after a quiet period takes ~50s | Free tier sleeping. Expected. $7/month removes it |
| Photo upload fails, everything else works | R2 credentials or bucket CORS. Check `/api/health` reports `storage: s3` |
| Deploy stuck "in progress", `/api/health` 500s | `STORAGE_PROVIDER=s3` without the four `S3_*` values. Fixed in `be55977`; the error now names them |
| A push to `main` produces no deploy | The Render GitHub App is not installed on the repo owner's account. A public repo can be cloned without it, but push events need it |

---

## What actually happened — 2026-08-09

Tasks 1–7 are done. Recorded here because two things did not go as the plan
assumed, and both are worth knowing before the domain moves.

**Deployed and verified.** Atlas M0 (Frankfurt), both Render services live from
`Furqan-10/NewHaulCheck`. Database round-trip 1.3 ms — Render and Atlas are in
the same region. Verified end to end: register, login, `/auth/me`, `/vehicles`,
`/dashboard`, the SPA rewrite on `/maintenance` and `/driver`, all four security
headers, CORS from the web origin, the 12-character password policy, and the
cron endpoint rejecting a request with no secret.

Deferred deliberately: R2 storage, Resend email, cron schedule, Google sign-in.
So uploads, invitations and password resets do not work yet. That is the
`storage: null, email: null` state reported by `/api/health`.

**1. The blueprint shipped a configuration that could not work.** `render.yaml`
pinned `STORAGE_PROVIDER=s3` while the four `S3_*` values were `sync: false`, so
the first deploy built, booted, then failed its health check on a bare
`KeyError` and hung. Setting `null` in the dashboard fixed it but left the
service one blueprint sync away from breaking again. Fixed in `be55977` — the
switch is `sync: false`, and selecting `s3` without credentials now raises
naming every missing variable.

Worth noting the plan told the operator to override this by hand, and the
override was still missed on the first run. An instruction that has to be
remembered is not a fix; the default has to be the safe one.

**2. Auto-deploy on push does not work yet, and it is the point of the exercise.**
Both services have `autoDeploy: yes`, but every deploy so far was triggered by
the blueprint sync or the API — none by a push. Render clones the repo fine
because it is public, but push events need the Render GitHub App installed on
`Furqan-10`, and the Render account is connected to a different GitHub user.
**Furqan has to approve that installation.** Until then, deploys work but must
be triggered manually, which is the original problem in a new place.

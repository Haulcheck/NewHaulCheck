# HaulCheck — deploy on Render and cut haulcheck.co.uk over from Emergent

**Date:** 2026-08-04
**Branch:** `ourhaul-deploy`
**Status:** Approved, ready for implementation planning
**Follows:** [2026-07-28 Emergent independence](2026-07-28-emergent-independence-deployment-design.md)

---

## 1. Goal

Make `haulcheck.co.uk` serve a build produced from a repository the team controls,
so that a commit reaches the live site.

Today it does not. The domain resolves to Emergent's hosting, which builds from
`github.com/dazwade620-beep/Haulcheck` — a repository nobody on this team pushes
to. Every change made since the Emergent export has been invisible to the live
site.

### Success criteria

1. A push to `main` on `github.com/Furqan-10/NewHaulCheck` reaches `haulcheck.co.uk`
   without anyone logging into a dashboard.
2. Frontend and API both answer under `haulcheck.co.uk`, on TLS, from Render.
3. No Emergent account, key, host or cookie is involved in serving the site.
4. The existing integration suite passes against the deployed API.
5. Running cost £0/month, with a documented upgrade path.

### Non-goals

- **Data migration from Emergent.** Confirmed to be client testing only. If that
  changes before cutover, an export becomes a separate task and this plan pauses.
- **Google sign-in.** Deliberately left off. See §7.
- **AI features.** Ship disabled, as before (`AI_PROVIDER=null`).
- **Repository restructuring.** The flattened layout on `ourhaul-deploy` stays.

---

## 2. Starting position

The 2026-07-28 design has been fully implemented on `ourhaul-deploy` (9 commits
past `merge-iters-30-32`, no divergence). `S3Storage` with SigV4 is written, the
`requirements.txt` installs off-platform, the Emergent script tag and
`@emergentbase/visual-edits` are gone from the bundle, `backend/Dockerfile` and
`render.yaml` exist, the cron endpoint exists, and `DEPLOYMENT.md` is written.

Nothing is deployed. The Render account holds four unrelated services.

### What the live domain does now

| Fact | Value |
|---|---|
| Nameservers | `ns1/2/3.livedns.co.uk` (Fasthosts) |
| Apex A records | `162.159.142.117`, `172.66.2.113` — Cloudflare, fronting Emergent |
| `www` | CNAME → apex |
| Response cookies | `__emg_vid`, `__emg_sid` — Emergent |
| `/api/health` | 404 — the live build predates Phase 5 |

Fasthosts is the registrar and DNS provider. There is no evidence of a Fasthosts
*hosting* package, and the app is not served from one.

### Why not host on Fasthosts

Considered and rejected. Their shared hosting is a PHP/MySQL or ASP.NET stack with
FTP upload, no root and no process supervisor; it cannot run a long-lived uvicorn
process or MongoDB. It could serve the static frontend, but not the API.

Their VPS product could run everything, and has real appeal — one origin, no
sleeping, UK-hosted. It was rejected because it costs money, ships none of the
required configuration (no `docker-compose.yml`, no nginx config, no TLS
automation, no deploy-on-push), and makes the team responsible for OS patching,
certificate renewal, database backups and crash recovery. That is a standing
operational burden in exchange for benefits that Render provides for nothing.

Fasthosts' current plans and pricing could not be verified directly — their site
blocks automated fetching. This does not affect the decision, which turns on
shared hosting being architecturally unable to run FastAPI at all.

---

## 3. Target architecture

```
Browser
   │
   ├─►  haulcheck.co.uk  +  www     ──►  Render Static Site  (free, global CDN)
   │        CRA build · REACT_APP_BACKEND_URL = https://api.haulcheck.co.uk
   │                                              │
   └─►  api.haulcheck.co.uk         ──►  Render Web Service (Docker, free, Frankfurt)
                                              ├──►  MongoDB Atlas M0  (free, Frankfurt)
                                              ├──►  Cloudflare R2     (free, 10 GB)
                                              └──►  Resend            (free, 3k/month)

   cron-job.org ──► POST api.haulcheck.co.uk/api/tasks/run-reminders · daily 07:00 UTC
```

Source of truth: `github.com/Furqan-10/NewHaulCheck` @ `main`. Both services
auto-deploy on push. This is the mechanism that solves the stated problem.

### Why this shape

**Render for both halves, not Render + Vercel.** The 2026-07-28 design paired
Render with Vercel because the frontend needed a CDN. Render's static sites
provide the same thing — global CDN, free, auto-deploy from GitHub, rewrite rules
and custom domains — so the second vendor buys nothing. One account, one
dashboard, one blueprint, one place to look when something breaks.

**Everything under `haulcheck.co.uk`.** The previous design accepted that Google
sign-in would be unavailable, because a `SameSite=None` session cookie
(`server.py:1804`) is blocked between two unrelated domains such as `vercel.app`
and `onrender.com`. Serving both halves from subdomains of one registrable domain
makes the cookie same-site and removes that constraint. The capability is
unlocked here even though enabling it is deferred (§7).

**Static site rather than serving the bundle from FastAPI.** Keeping them separate
means the frontend stays up on the CDN while the free-tier API sleeps, so a
prospect sees the application shell rather than a blank fifty-second wait.

---

## 4. Repository work

### 4.1 `render.yaml` — add the static site

A second service block alongside `haulcheck-api`:

```yaml
  - type: web
    name: haulcheck-web
    runtime: static
    rootDir: frontend
    buildCommand: yarn install --frozen-lockfile && GENERATE_SOURCEMAP=false yarn build
    staticPublishPath: ./build
```

It carries the SPA rewrite (`/* → /index.html`, so React Router deep links
resolve), the security headers and the immutable cache header on `/static/*`.
`REACT_APP_BACKEND_URL` is declared `sync: false` — it is baked in at build time,
so changing it requires a rebuild, not a restart.

`engines.node` is already pinned to `20.x`, so the Node version will not drift.

### 4.2 Delete `frontend/vercel.json`

Its entire contents — framework, build command, output directory, SPA rewrite,
security headers, cache headers — move into `render.yaml`. Leaving the file in
place would leave a second, silently ignored deployment configuration for the next
person to be misled by.

### 4.3 Fill in the production domain

The Emergent URLs are already gone from this branch — the 2026-07-28 work removed
them rather than replacing them, on the reasoning that a canonical pointing at the
wrong host is worse than no canonical at all. What remains in
`frontend/public/index.html`, `robots.txt` and `sitemap.xml` is three commented-out
placeholders reading `https://app.example.com`, each with a note saying to complete
it once the production domain exists.

It exists. Fill all three in with `https://haulcheck.co.uk`, uncomment them, and
add the two `<url>` entries to the sitemap.

The guard test `backend/tests/test_no_third_party_frontend.py` already fails the
build if any Emergent or PostHog host reappears in the bundle, so this change
cannot regress that.

### 4.4 Rewrite `DEPLOYMENT.md` step 5

Step 5 is written against Vercel. Replace with the Render static site. Update step
6 (connecting the two) accordingly, and fold "When the domain arrives" into the
main flow with the actual records from §6 — the domain is no longer hypothetical.

---

## 5. Reconciling the repositories

`NewHaulCheck` @ `main` (`2d351e3`) is content-identical to `ourhaul-deploy`
(`0b44db0`) apart from three lines in `DEPLOYMENT.md`, but its history is
unrelated — two import commits rather than the real 26. A plain push is rejected.

**Decision: force-push the real history once.** Nothing has been built on those two
commits, so nothing is lost, and afterwards every push is an ordinary `git push`.

Their three lines must be carried across first, not discarded: `2d351e3` corrects
the repository URL in `DEPLOYMENT.md` from `Furqan-10/OUR-Haul` to
`Furqan-10/NewHaulCheck`, which is the correct value going forward. Two of the
three (the prerequisites section and step 4) survive as-is; the third is inside
step 5, which §4.4 rewrites for Render anyway. Force-pushing without folding these
in first would silently reinstate a stale repository URL in the guide the client
follows.

**This requires Furqan's agreement before it runs** — it is their repository, and
a force-push is not reversible from the other side. The implementation plan treats
it as a gate, not a step.

Then add `newhaul` as a local remote so subsequent pushes are routine.

---

## 6. The cutover

Provisioning order matters: nothing touches DNS until the stack is proven on its
`onrender.com` URLs.

1. Atlas M0 (Frankfurt), database user, network access
2. Cloudflare R2 bucket, API token, bucket CORS
3. Resend API key and sender
4. Render Blueprint from `NewHaulCheck` — creates both services at once
5. Fill the `sync: false` variables
6. Smoke test on `*.onrender.com`
7. DNS (below)
8. Set `CORS_ORIGINS` and `REACT_APP_BACKEND_URL` to the real domain, **rebuild the
   frontend** — the old API URL is compiled into the existing bundle
9. cron-job.org against `/api/tasks/run-reminders`
10. Integration suite against the live API

### DNS at Fasthosts

| Record | Type | Value |
|---|---|---|
| `@` | A | `216.24.57.1` — replaces **both** existing Cloudflare A records |
| `www` | CNAME | `<static-site>.onrender.com` — no longer pointing at the apex |
| `api` | CNAME | `<web-service>.onrender.com` |

Delete any `AAAA` records. Render is IPv4-only and stray AAAA records cause
intermittent, hard-to-diagnose failures.

Render creates and renews TLS certificates automatically and redirects HTTP to
HTTPS.

**Sequencing.** Lower TTL to 300s a day ahead, so a rollback is minutes rather than
hours. Add `api` first and confirm it answers. Only then swap `@` and `www`
together — split across two changes, the frontend would be live against an API the
browser cannot reach.

**Rollback** is restoring the two original A records and the `www` CNAME. Keep them
recorded before making any change.

---

## 7. Google sign-in stays off

The architecture now supports it, and the code path is built (`backend/oauth.py`).
It is deferred anyway: enabling it during a cutover adds a Google Cloud OAuth
client and two environment variables to a change that is already touching hosting,
DNS, storage, database and email at once.

The button hides itself while `GOOGLE_CLIENT_ID` is unset, so no user sees a broken
control. Email/password is unaffected and remains the primary path. Turning it on
later is a ten-minute follow-up, documented in `DEPLOYMENT.md`.

---

## 8. Testing

The existing suite is integration-style over HTTP, so after deployment it doubles
as the acceptance gate. Point it at `https://api.haulcheck.co.uk` and run
`pytest -n 0`. Warm the instance with a request first — the free tier's cold start
exceeds most default HTTP timeouts and the first test would otherwise fail for the
wrong reason.

The smoke checklist matters as much as the suite, because it covers the parts only
a real deployment exercises:

- Register and log in — proves Atlas connectivity and `JWT_SECRET`
- Upload a defect photo — proves R2 and the SigV4 implementation against a real bucket
- Generate a PDF audit pack — proves reportlab and attachment merging under the container
- Fire the cron endpoint by hand — proves `CRON_SECRET` and the Mongo lock
- Load a React Router deep link directly — proves the SPA rewrite
- Confirm no request in the browser's network tab goes to an Emergent host

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Free-tier cold start (~50s) reads as an outage | Frontend is CDN-served and loads instantly; cron ping keeps the API warmer. $7/mo removes it — do this before any live demo |
| Atlas M0 has no automated backups | `mongodump` before the client onboards a real operator; M10 upgrade documented |
| The Emergent deployment is left running | Have the client shut it down after cutover. Two live copies against two databases is a support problem nobody will diagnose quickly |
| SigV4 signing wrong against a real bucket | Verified offline against AWS test vectors; exercised against a scratch bucket before real evidence is stored |
| DNS propagation strands users mid-swap | TTL lowered to 300s beforehand; `api` proven before the apex moves; original records recorded for rollback |
| Force-push rejected or contested | Gated on Furqan's agreement before any deployment work depends on it |
| Client-side data assumed disposable | Confirmed as testing only. Re-confirm immediately before cutover; if real records exist, stop and scope an export |

---

## 10. Decisions and their reasons

| Decision | Reason |
|---|---|
| Render for both halves | Render static sites match what Vercel was chosen for; a second vendor buys nothing |
| Not Fasthosts shared hosting | Cannot run an ASGI process or MongoDB. Architecturally impossible, not a tuning problem |
| Not a Fasthosts VPS | Costs money and transfers patching, TLS, backups and crash recovery to the team, for benefits Render gives free |
| Atlas M0 over self-hosted Mongo | No server to own; matches the existing `render.yaml` and `DEPLOYMENT.md` |
| `NewHaulCheck` as source of truth | Client, partner and developer all have access — the others are personal accounts |
| Force-push over merging unrelated histories | Nothing built on the two import commits; keeps provenance and makes future pushes ordinary |
| Google sign-in deferred | Cutover already touches five subsystems; the button hides itself and the work is ten minutes later |
| `api` subdomain before apex swap | Frontend live against an unreachable API is a worse failure than a slightly longer cutover |
| No data migration | Confirmed testing data only, re-confirmed at cutover |

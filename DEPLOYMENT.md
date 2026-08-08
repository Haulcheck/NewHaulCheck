# HaulCheck — deployment guide

**Putting HaulCheck online, on infrastructure you control, for £0/month.**

This is the internet-facing deployment. For running the app on a single laptop,
see [CLIENT_SETUP.md](CLIENT_SETUP.md) instead.

> **Written from the code and configuration in this repository, not from a
> completed deployment** — the accounts have to be created in your name. The
> steps match what the repo expects; the dashboards may have moved a button
> since. Where a screen differs, the value you need is still the one named here.

---

## What you are building

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

Four services. Each has a free tier that this app fits inside.

| Service | Free tier | What happens when you outgrow it |
|---|---|---|
| **Render** — the API | 750 hours/month, sleeps after 15 min idle | $7/month, no sleeping |
| **MongoDB Atlas** — the database | 512 MB (M0) | ~$9/month for 2 GB |
| **Cloudflare R2** — files | 10 GB, no charge to serve | $0.015/GB/month after |
| **Resend** — email | 3,000/month, 100/day | $20/month for 50,000 |
| **Render** — the web app | Free static site on a global CDN | Included in the same plan |

**Budget 45–60 minutes.** Most of it is waiting for accounts to verify.

---

## Before you start

Create these four accounts. Use the client's own email — these hold their data,
and you want them to own it.

1. **MongoDB Atlas** — <https://www.mongodb.com/cloud/atlas/register>
2. **Cloudflare** — <https://dash.cloudflare.com/sign-up>
3. **Resend** — <https://resend.com/signup>
4. **Render** — <https://dashboard.render.com/register> (sign in with GitHub)

Plus **cron-job.org** (<https://console.cron-job.org/signup>) at the end.

You also need the code on GitHub. It already is:
`https://github.com/Furqan-10/NewHaulCheck`.

Keep a scratch file open. You will collect six values as you go, and step 4
needs all of them at once.

---

## Step 1 — The database

There is no schema to create. The app builds its own collections and indexes
the first time it starts.

1. In Atlas, **Create a deployment** → choose **M0** (the free one).
2. Provider **AWS**, region **Frankfurt (eu-central-1)** — closest to the UK and
   Ireland, and it keeps the data in the EU.
3. Name it `haulcheck`. Create.
4. Atlas prompts for a database user. Create one, let it generate the password,
   and **copy the password now** — it is not shown again.
5. **Network Access** → **Add IP Address** → **Allow access from anywhere**
   (`0.0.0.0/0`).

   > This looks alarming and is the correct choice here. Render's free tier has
   > no fixed outbound IP address, so there is nothing to add to an allow-list.
   > The database password is what protects it. If you later move to a paid
   > Render plan with a static IP, narrow this to that address.

6. **Database** → **Connect** → **Drivers** → copy the connection string. It
   looks like:

   ```
   mongodb+srv://haulcheck:<db_password>@haulcheck.ab12cde.mongodb.net/?retryWrites=true&w=majority
   ```

7. Replace `<db_password>` with the real password. If the password contains
   `@`, `/`, `:` or `#`, percent-encode it (`@` → `%40`) or the URL will not parse.

**Write down:** `MONGO_URL`

---

## Step 2 — File storage

This holds defect photos, signed walkaround sheets and insurance certificates.

1. Cloudflare dashboard → **R2** → **Create bucket**.
2. Name it `haulcheck`. Location **EU**. Create.
3. **Manage R2 API Tokens** → **Create API token**.
4. Permissions: **Object Read & Write**. Scope it to the `haulcheck` bucket
   only — a token that can reach every bucket in the account is a token you
   cannot safely put in a deploy dashboard.
5. Create, then copy **Access Key ID** and **Secret Access Key**. The secret is
   shown once.
6. Note your **Account ID** — it is in the R2 sidebar. Your endpoint is:

   ```
   https://<account-id>.r2.cloudflarestorage.com
   ```

**Write down:** `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT`

---

## Step 3 — Email

Used for defect alerts, reminder digests, invitations, password resets and
audit packs.

1. Resend → **API Keys** → **Create API Key**. Permission **Sending access**.
   Copy it.
2. **Sender address.** Without a verified domain, Resend only delivers to the
   address that owns the account. That is fine for testing. For real use, add
   the client's domain under **Domains** and set the DNS records it lists.

**Write down:** `RESEND_API_KEY`, `SENDER_EMAIL`

---

## Step 4 — The API

1. Render dashboard → **New** → **Blueprint**.
2. Connect the `Furqan-10/NewHaulCheck` repository. Render reads
   [`render.yaml`](render.yaml) and proposes **two** services: `haulcheck-api`
   and `haulcheck-web`. Both come from the one file — the web app is set up in
   step 5, not separately.

   > **Check that pushing actually deploys.** Render can clone a public
   > repository without any special permission, so the first build succeeds
   > whether or not this is set up — but it only receives *push* events if its
   > GitHub App is installed on the account that **owns** the repository, with
   > that repository selected. Get this wrong and everything looks fine until
   > you notice your changes never reach the site.
   >
   > Install it at <https://github.com/apps/render/installations/new>. Only an
   > owner of that GitHub account can do it, so if the repository belongs to
   > someone else, they have to click it themselves.
   >
   > Verify by pushing a commit and watching the service's Events tab. A working
   > setup shows a deploy triggered by the push. If the only entries are
   > "Triggered by you", it is not connected and every deploy will need a click.
3. It will ask for the values marked `sync: false`. Fill in:

   | Variable | Value |
   |---|---|
   | `MONGO_URL` | from step 1 |
   | `S3_BUCKET` | `haulcheck` |
   | `S3_ENDPOINT` | from step 2 |
   | `S3_ACCESS_KEY` | from step 2 |
   | `S3_SECRET_KEY` | from step 2 |
   | `RESEND_API_KEY` | from step 3 |
   | `SENDER_EMAIL` | from step 3 |
   | `CORS_ORIGINS` | `https://haulcheck-web.onrender.com` — a placeholder for now, corrected in step 6 |

   Everything else is already set in `render.yaml`. `JWT_SECRET` and
   `CRON_SECRET` are generated for you; do not invent your own.

4. **Apply**. The first build takes 5–10 minutes.

**The build is the moment things go wrong.** Watch the log. It should end with
the app starting and reporting:

```
Indexes ready: 64 created/verified
Storage provider: s3 (reachable)
AI provider: null
Email provider: resend
```

`Storage provider: s3 (reachable)` is the line that proves R2 works.

**Write down:** the service URL, e.g. `https://haulcheck-api.onrender.com`

Check it:

```bash
curl https://haulcheck-api.onrender.com/api/health
```

The first request wakes a sleeping instance and can take **50 seconds**. That
is the free tier, not a fault.

---

## Step 5 — The web app

The blueprint in step 4 already created this alongside the API — Render reads
both services from the same `render.yaml`, including the build command, the
security headers and the rewrite that makes React Router's deep links work. This
step only fills in its one variable.

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

---

## Step 6 — Connect the two

The API refuses requests from origins it does not know. Right now it does not
know your web app URL.

1. Render → `haulcheck-api` → **Environment**.
2. Set `CORS_ORIGINS` to the exact web app URL from step 5 — scheme included, no
   trailing slash:

   ```
   https://haulcheck-web.onrender.com
   ```

3. Save. Render redeploys automatically.

> **Why the app refuses to start without this.** In production the API rejects
> an unset or `*` value outright. A wildcard cannot be combined with credentials
> — browsers reject it, and the framework then quietly echoes back whichever
> origin asked, which is every origin, with cookies attached. Failing at startup
> is the safe version of that mistake.

> **Render preview environments will not work** against this API, and that is
> deliberate. Each preview gets its own URL, and this list also validates OAuth
> redirect URIs. Widening it to a wildcard would let any subdomain call a
> credentialed API. Test on the real URL.

---

## Step 7 — Daily reminders

The reminder jobs run inside the API at 07:00 UTC. On the free tier the instance
is asleep at 07:00, so they never run — silently, because nothing is awake to
log it. An external scheduler calls the API instead, which also wakes it.

1. Render → **Environment** → copy the generated value of `CRON_SECRET`.
2. cron-job.org → **Create cronjob**.
3. Configure:

   | Field | Value |
   |---|---|
   | Title | HaulCheck daily reminders |
   | URL | `https://haulcheck-api.onrender.com/api/tasks/run-reminders` |
   | Schedule | Every day at **07:00**, timezone **UTC** |
   | Request method | **POST** |
   | Header | `Authorization: Bearer <CRON_SECRET>` |

4. Save, then use **Test run**. Expect `200` and a body like
   `{"ran": {"daily": {"orgs": 1, "sent": 0, "failed": 0}}}`.

One entry covers both jobs: it runs the daily job every day and adds the weekly
one on Mondays, matching the built-in schedule.

> Calling it twice is safe. Both jobs take a lock in the database, so a second
> call returns `{"daily": null}` and sends nothing. `null` means "another run
> held the lock", which is different from "ran and had nothing to send".

---

## Step 8 — Smoke test

Work through this after every deploy. Each step exercises a different part of
the stack, so where it stops tells you what is broken.

- [ ] `https://<render-url>/api/health` returns JSON.
      Check `"storage": "s3"` — `"null"` means the R2 variables did not take.
- [ ] The web app URL loads the login page.
- [ ] Open a deep link directly, e.g. `<web-url>/maintenance`, and hard-refresh
      it. *(The SPA rewrite. Without it this 404s while the root still works.)*
- [ ] Register an account. *(API, database write, password policy — minimum 12
      characters.)*
- [ ] Sign in. *(Token issue and verify.)*
- [ ] Add a vehicle with an MOT date in the past; it shows as expired.
      *(Compliance calculation.)*
- [ ] Raise a defect and attach a photo. *(R2 upload — the step most likely to
      fail.)*
- [ ] Reopen the defect; the photo displays. *(R2 download.)*
- [ ] Generate a PDF audit pack and open it. *(PDF rendering in the container.)*
- [ ] Add a repair and a recall. *(The iteration 30–32 features.)*
- [ ] Create a driver, open `/driver`, sign in with the access code.
      *(The separate driver authentication path.)*
- [ ] Trigger reminders:
      ```bash
      curl -X POST -H "Authorization: Bearer $CRON_SECRET" \
        https://<render-url>/api/tasks/run-reminders
      ```
      Expect `{"ran": {...}}` and an email. *(Resend, and the cron path.)*
- [ ] Run the same command again. Expect `"daily": null` — the lock preventing
      duplicate email.

### Running the test suite against the deployment

```bash
cd backend
pip install -r requirements-dev.txt
export REACT_APP_BACKEND_URL=https://haulcheck-api.onrender.com
curl -s "$REACT_APP_BACKEND_URL/api/health"   # wake it first
pytest -n 0
```

The wake-up call is required, not optional — a cold start outruns the default
HTTP timeout and the whole suite fails at once.

---

## What the free tier actually costs you

Worth knowing before a demo, so nothing is a surprise in front of the client.

- **50-second first load.** Render stops the API after 15 minutes idle. The web
  app is a static site on the CDN and never sleeps, so the login page appears
  instantly and then waits on the API. Before any live demo, open the app a
  minute early — or move to the $7/month plan, where this stops happening.
  Only the API has this behaviour; static sites are always on.
- **Atlas M0 is 512 MB.** Uploaded files go to R2, not the database, so this
  holds a lot of fleet records — but it does not grow, and there are no
  automated backups on M0.
- **Resend allows 100 emails/day** on the free plan. A large fleet's daily
  reminder digest can approach that.

---

## Step 9 — The domain

The domain is `haulcheck.co.uk`, registered at Fasthosts. DNS is managed there
too — the nameservers are `ns1.livedns.co.uk`, `ns2` and `ns3`.

It currently points at the old Emergent deployment. This step moves it, and it
is the only step the public can see.

**A day before:** Fasthosts control panel → DNS → lower the TTL on the existing
records to 300 seconds. Propagation is governed by the *old* TTL, so this has to
be done ahead of the change, not during it. Skip it and a mistake takes hours to
undo instead of minutes.

**Record the current values before changing anything** — this is the rollback:

| Record | Type | Current value |
|---|---|---|
| `@` | A | `162.159.142.117` |
| `@` | A | `172.66.2.113` |
| `www` | CNAME | `haulcheck.co.uk` |

1. **Render** → `haulcheck-api` → **Settings** → **Custom Domain** → add
   `api.haulcheck.co.uk`.
2. **Fasthosts** → add `api` as a CNAME to `haulcheck-api.onrender.com`. Wait
   for Render to mark the domain verified, then confirm
   `https://api.haulcheck.co.uk/api/health` answers before going further.
3. **Render** → `haulcheck-web` → **Custom Domain** → add both
   `haulcheck.co.uk` and `www.haulcheck.co.uk`.
4. **Fasthosts** — now the visible change, all three together:
   - Delete **both** apex `A` records above. Add one `A` record on `@` pointing
     to `216.24.57.1`.
   - Change `www` from a CNAME to the apex into a CNAME to
     `haulcheck-web.onrender.com`.
   - Delete any `AAAA` records. Render is IPv4-only, and a stray `AAAA` produces
     a site that works for you and intermittently fails for other people —
     which is the hardest kind of fault to be told about.
5. **Render** → `haulcheck-api` → set `CORS_ORIGINS` to `https://haulcheck.co.uk`.
6. **Render** → `haulcheck-web` → set `REACT_APP_BACKEND_URL` to
   `https://api.haulcheck.co.uk`, then **Manual Deploy**. The old value is
   compiled into the current bundle; saving the variable alone changes nothing.
7. TLS is automatic. Render issues and renews the certificates and redirects
   HTTP to HTTPS. No action, no cost.

> **Why `api` goes first.** If the apex moved first, the web app would be live on
> haulcheck.co.uk while still compiled against an API the browser cannot reach —
> a site that loads and then fails at login. Proving `api` first means the last
> change is the only visible one.

**Rollback:** restore the two `A` records and the `www` CNAME from the table
above. At a 300-second TTL this takes effect in minutes.

**Afterwards:** ask the client to shut down the old Emergent deployment. Leaving
it running means two live copies of a compliance product against two separate
databases, and nobody will work out quickly which one a given record is in.

### Optional: turn on Google sign-in

Now possible, because the web app and the API share `haulcheck.co.uk`.

- Google Cloud Console → **APIs & Services** → **Credentials** → **Create OAuth
  client ID** → **Web application**.
- Authorised redirect URI, exactly: `https://haulcheck.co.uk/auth/google/callback`
- Add `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` on `haulcheck-api`.
- The button appears on the login page by itself. It stays hidden while those two
  are unset, so nobody sees a broken control in the meantime.

> **Why this needed the domain.** It relies on a session cookie marked
> `SameSite=None`, which Safari and other privacy-focused browsers block between
> two unrelated domains. `haulcheck.co.uk` and `api.haulcheck.co.uk` share a
> registrable domain, so the cookie is same-site and every browser accepts it.
> Email/password sign-in never had this problem and works throughout.

---

## When you want AI switched on

Five features are built and currently disabled: defect summaries, letter
drafting, insurance-certificate import, the fleet risk briefing, and tacho
printout analysis. Each one degrades on its own — the app tells the user the
feature is not enabled and carries on.

To enable:

1. Add `anthropic` to [`backend/requirements.txt`](backend/requirements.txt).
2. On Render, set `AI_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=...`.
3. Redeploy.

Expect single-digit dollars a month at this usage — these are per-click
features, not background processing.

> Two of the five read documents: insurance-certificate import and tacho
> printout analysis extract structured compliance fields from a photo or PDF.
> That is where model quality shows first, so test those two before trusting
> them with real records.

---

## Going paid

When the client has customers:

| Change | Cost | Why |
|---|---|---|
| Render Starter | $7/mo | No sleeping. The 07:00 job runs on its own; keep the cron as a backstop |
| Atlas M10 | ~$9/mo | Automated backups, more room |
| Resend | $20/mo | 50,000 emails |

> The web app is not on this list, and that is worth knowing rather than
> assuming: Render's static sites have no commercial-use restriction and no
> paid tier this app would ever need. That half stays free permanently. Several
> of the obvious alternatives charge around $20/month the moment a product is
> commercial, so it is the sort of line that is easy to be surprised by later.

**Back up the database before you have customers, not after.** Atlas M0 has no
automated backups:

```bash
mongodump --uri="$MONGO_URL" --out=backup-$(date +%F)
```

---

## When something is wrong

| Symptom | Cause |
|---|---|
| Build fails during `pip install` | Check `requirements.txt` was not reverted to the Emergent version — that one fetches a package from a URL that only resolves inside their image |
| App will not start, no useful error | `MONGO_URL`, `DB_NAME` or `JWT_SECRET` missing. All three are read as the app loads, so it cannot start without them |
| `CORS_ORIGINS must list explicit origins` | Expected. Set it to the web app URL (step 6) |
| Database connection fails, wrong-password look | The `mongodb+srv://` form needs a DNS lookup. Confirm `dnspython` is in `requirements.txt`, and that a `@` or `/` in the password is percent-encoded |
| Every browser request blocked by CORS | `CORS_ORIGINS` does not match the web app URL exactly — scheme, no trailing slash |
| Deep links 404 on refresh, the root works | The rewrite in `render.yaml` is missing or is not the last route |
| Site works for you, intermittently fails for others | A stray `AAAA` record. Render is IPv4-only |
| Uploads fail | `/api/health` shows `"storage"`. `"null"` means the `S3_*` variables did not take. `SignatureDoesNotMatch` in the log means a wrong key or a region other than `auto` |
| Login works, everything else 401 | `REACT_APP_BACKEND_URL` has a trailing `/api`. Remove it and redeploy the frontend |
| One user's actions rate-limit everyone | `TRUST_PROXY_HEADERS` is not `1`. Render terminates TLS at a proxy, so every request looks like one IP |
| Reminders never arrive | The cron job is not configured, or its `Authorization` header is wrong. Use **Test run** on cron-job.org |
| First load takes ~50 seconds | The free tier stopped the instance. Expected |

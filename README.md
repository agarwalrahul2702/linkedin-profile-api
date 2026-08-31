# LinkedIn Profile API (POC)

Accepts a LinkedIn profile URL and returns structured JSON (name, headline,
location, about, experience, education, skills, certifications, languages,
profile image) by calling LinkedIn's internal "Voyager" API — the same
private JSON API `linkedin.com` itself uses once you're logged in.

> **Status:** proof of concept. Not deployed yet, not hardened. See
> [Known limitations](#known-limitations) before relying on this for
> anything real.

## Approach

LinkedIn does not offer a public API for reading arbitrary profiles at this
level of detail. Instead, this project:

1. Reuses an authenticated LinkedIn session over direct HTTP. This is also
   the authentication model documented by PhantomBuster: a LinkedIn `li_at`
   session cookie plus the matching browser user-agent, not stored LinkedIn
   login credentials. `JSESSIONID` is bootstrapped directly from LinkedIn when
   it is not supplied. The optional experimental login helper can submit the
   classic LinkedIn login form without launching or controlling a browser.
2. Calls LinkedIn's decorated `voyager/api/identity/dash/profiles` endpoint
   directly with `q=memberIdentity`, using those cookies plus a CSRF token
   derived from `JSESSIONID`. It currently tries
   `FullProfileWithEntities-109`, then a configurable `-101` compatibility
   fallback if LinkedIn rejects the newer schema. The retired `profileView`
   endpoint is not used.
3. Parses both response families LinkedIn currently exposes: standard REST.li
   `elements[]` documents and normalized `data` + `included[]` entity graphs.
   Both are mapped into the same flat, documented JSON schema.

The runtime API uses a "bring your own session cookies" model; it never needs
a browser or a password while serving profile requests (see [Auth](#auth)).

## Setup

Python 3.9 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env — see "Auth" below for the required session configuration
uvicorn app.main:app --reload
```

The API is then available at `http://localhost:8000`. Interactive docs
(Swagger UI) are at `http://localhost:8000/docs`.

### Local dashboard

Open `http://localhost:8000/` for the two-panel web interface. It provides:

- in-memory LinkedIn session login through direct backend HTTP;
- profile URL lookup with loading and error states;
- structured profile cards for experience, education, skills,
  certifications, and languages;
- sample-data preview and one-click JSON copying;
- responsive desktop and mobile layouts.

The dashboard sends the password only to the local FastAPI process for the
single login request. It is not written to `.env`, browser storage, logs, or
the API response. The resulting session exists in memory until the server
restarts. LinkedIn calls still happen entirely in the backend; the dashboard
does not open or automate linkedin.com. Credential login is restricted to
loopback clients by default; do not enable `ALLOW_REMOTE_UI_LOGIN` unless the
app is protected by HTTPS and your own access-control layer.

### Auth

The stable flow is to provide an already authenticated `li_at` and the exact
matching user-agent through environment variables. That mirrors
PhantomBuster's documented connection model. `JSESSIONID` is optional: the
client obtains that CSRF cookie over direct HTTP if omitted. Never paste these
values into source code or send them to another person.

An experimental no-browser helper is also included. It submits LinkedIn's
classic login form directly and saves only the resulting session cookies to
the ignored `.env` file:

```bash
python scripts/linkedin_login.py
```

The email is read from the terminal and the password uses a hidden prompt.
Neither credential is written to disk. On successful login, the helper writes
`LI_AT`, `JSESSIONID`, and `LINKEDIN_USER_AGENT` to `.env`, changes the file to
owner-only permissions (`0600`), and asks you to restart the API.

If LinkedIn changes the login flow or requires MFA, CAPTCHA, or another
checkpoint, the helper stops. It does not attempt to bypass security
challenges. Quoted and unquoted `JSESSIONID` values are both accepted.

These cookies expire (LinkedIn will eventually force a re-login, and may
invalidate them sooner if it flags the session as suspicious) — see
limitations below.

## API

### `POST /api/v1/profile`

**Request**
```json
{ "linkedin_url": "https://www.linkedin.com/in/someone/" }
```

**Response** `200 OK`
```json
{
  "public_identifier": "jane-doe",
  "profile_url": "https://www.linkedin.com/in/jane-doe/",
  "name": "Jane Doe",
  "headline": "Senior Engineer at Example Co.",
  "location": "Bengaluru, Karnataka, India",
  "about": "...",
  "profile_image_url": "https://media.licdn.com/...",
  "background_image_url": "https://media.licdn.com/...",
  "experience": [
    {
      "title": "Senior Engineer",
      "company": "Example Co.",
      "location": "Bengaluru",
      "date_range": "06/2022 - Present",
      "description": "..."
    }
  ],
  "education": [
    {
      "school": "Example University",
      "degree": "B.Tech",
      "field_of_study": "Computer Science",
      "date_range": "2016 - 2020"
    }
  ],
  "skills": ["Python", "Distributed Systems"],
  "certifications": [
    { "name": "AWS Certified Solutions Architect", "authority": "AWS", "date_range": "2021" }
  ],
  "languages": [
    { "name": "English", "proficiency": "Native or bilingual" }
  ]
}
```

**Error responses**

| Status | Meaning |
|---|---|
| 400 | URL doesn't look like a LinkedIn profile URL |
| 401 | Session cookies missing, expired, or rejected by LinkedIn |
| 404 | Profile not found / not visible to the current session |
| 429 | LinkedIn is throttling this session — back off |
| 502 | LinkedIn returned an unexpected or malformed response |

### `POST /api/v1/session/login`

Creates or replaces the running process's in-memory LinkedIn session using a
direct HTTP login. Intended for the local dashboard; the password is not
persisted.

```json
{
  "linkedin_id": "name@example.com",
  "password": "entered-once",
  "user_agent": "Mozilla/5.0 ..."
}
```

Returns `401` if LinkedIn rejects the login or requires MFA, CAPTCHA, or a
checkpoint.

### `POST /api/v1/profiles` (batch)

Fetch multiple profiles concurrently (bounded by `MAX_BATCH_CONCURRENCY`,
default 5). Each URL is resolved independently — one bad URL doesn't fail
the whole batch.

**Request**
```json
{ "linkedin_urls": ["https://linkedin.com/in/a/", "https://linkedin.com/in/b/"] }
```

**Response** `200 OK`
```json
{
  "results": [
    { "linkedin_url": "https://linkedin.com/in/a/", "profile": { "...": "..." }, "cached": false },
    { "linkedin_url": "https://linkedin.com/in/b/", "error": "No profile found for identifier 'b'." }
  ]
}
```
Capped at `MAX_BATCH_SIZE` URLs per request (default 20) — returns `400` above that.

### `GET /health`

Basic liveness check (no auth).

### `GET /health/linkedin-session`

Cheap check of whether the server's configured `li_at` session is still
valid, without doing a full profile fetch. Useful to
catch "cookies expired" as a clear signal instead of every profile
request failing mysteriously.

```json
{ "valid": true, "detail": "Session is active." }
```

## Caching & retries

- **Caching**: successfully parsed profiles are cached in-memory for
  `CACHE_TTL_SECONDS` (default 300s), keyed by public identifier. Repeat
  lookups of the same profile within the TTL skip LinkedIn entirely —
  faster for callers and lower ban risk for the underlying account.
- **Retries**: transient failures (timeouts, LinkedIn 5xx) are retried up
  to 3 times with exponential backoff. Auth failures, 404s, and 429s from
  LinkedIn are *not* retried, since retrying wouldn't help.
- **Concurrency guard**: no more than `LINKEDIN_MAX_CONCURRENCY` requests
  (default 3) are sent to LinkedIn concurrently in one server process,
  even when multiple single and batch API calls arrive at once.
- **No auth on this API itself right now** — every endpoint is open to
  anyone who reaches the URL. Fine for a low-traffic personal deployment;
  reconsider (an API key, an IP allowlist, or putting it behind a VPN)
  before pointing this at meaningful traffic, since your LinkedIn
  session's ban risk scales with how many requests actually go out. The
  concurrency guard is not an API rate limiter.

## Tests

The automated tests use representative, local Voyager response fixtures and
mock clients. They never contact LinkedIn and do not require credentials.

```bash
pip install -r requirements-dev.txt
pytest -q
```

Before deployment, also configure a real session and perform the manual
smoke test below with a profile your account can view:

```bash
curl http://localhost:8000/health/linkedin-session
curl -X POST http://localhost:8000/api/v1/profile \
  -H 'content-type: application/json' \
  -d '{"linkedin_url":"https://www.linkedin.com/in/example/"}'
```

## Known limitations

- **This relies on reverse-engineered, undocumented behavior.** LinkedIn
  can change the Voyager response shape, field names, query/decorator IDs, or
  endpoint paths at any time. The configurable primary/fallback decorators and
  dual parser reduce that failure mode but cannot eliminate it.
- **This violates LinkedIn's User Agreement**, which prohibits scraping
  and automated data collection. LinkedIn has a track record of pursuing
  legal action and banning accounts/IPs associated with scraping (e.g. the
  hiQ Labs litigation, and more recent enforcement/lawsuits against scraper
  operators). Treat this as a technical proof of concept, not something to
  run against production traffic or third parties' accounts.
- **Session cookies expire and can be invalidated early** if LinkedIn's
  anti-automation systems flag unusual request patterns from the account
  (e.g. many profile lookups in a short window). The API does not attempt
  CAPTCHA solving or automatic reauthentication.
- **The cache is process-local, in-memory only.** Fine for a single
  instance; a multi-instance deployment would need it backed by something
  shared (Redis) or duplicate lookups will still hit LinkedIn per instance.
- **The concurrency guard is in-memory and per-process.** It bounds parallel
  LinkedIn calls but does not enforce a per-client or per-minute request rate.
- **Dashboard-created sessions are memory-only.** Restarting the API clears
  the session, which intentionally avoids saving credentials or new cookies.
- **Only public-page-equivalent fields are parsed.** Fields like contact
  info, recommendations, and posts/activity aren't covered by this POC's
  parser, though they're available from other Voyager endpoints if needed
  later.
- **Not deployed.** Runs locally only for now; HTTPS deployment is a
  follow-up step.

## Project structure

```
app/
  main.py            FastAPI routes (single + batch + health checks)
  linkedin_client.py Async Voyager API client (auth, URL validation, retries,
                     error mapping)
  cache.py            In-memory TTL cache for parsed profiles
  parser.py          Raw Voyager JSON -> clean response schema
  models.py          Pydantic request/response models
  config.py          Env-based config (session, cache, batch limits)
  static/            Local dashboard (HTML, CSS, vanilla JavaScript)
scripts/
  linkedin_login.py  Direct-HTTP login helper (hidden password prompt)
requirements.txt
requirements-dev.txt
tests/
.env.example
```

"""
Configuration / session credentials.

Auth is intentionally small for the POC: the API expects a valid LinkedIn
`li_at` session supplied via the environment. `JSESSIONID` may also be
supplied, but the client can bootstrap that non-authentication CSRF cookie
from LinkedIn's classic login page over direct HTTP.

To get these values for your own account:
  1. Log into linkedin.com in a normal browser.
  2. Open DevTools -> Application -> Cookies -> https://www.linkedin.com
  3. Copy `li_at` and the exact browser user-agent.
  4. Put them in a local `.env` file (see .env.example). Never commit .env.
"""

import os
from dotenv import load_dotenv

load_dotenv()

LI_AT = os.getenv("LI_AT", "").strip()


def _normalize_jsessionid(value: str) -> str:
    """Accept copied cookie values with or without surrounding quotes."""
    return value.strip().strip('"').strip("'")


JSESSIONID = _normalize_jsessionid(os.getenv("JSESSIONID", ""))

LINKEDIN_BASE_URL = "https://www.linkedin.com"
VOYAGER_BASE_URL = f"{LINKEDIN_BASE_URL}/voyager/api"
PROFILE_DECORATION_ID = os.getenv(
    "PROFILE_DECORATION_ID",
    "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-109",
)

# LinkedIn rolls decoration versions independently of the endpoint path. A
# 400/410 from the newest version can therefore mean schema drift rather than
# a missing member. Keep one known older normalized-JSON decoration as a
# compatibility fallback. Operators can replace the comma-separated list
# without changing code when another rollout happens.
PROFILE_DECORATION_FALLBACK_IDS = tuple(
    value.strip()
    for value in os.getenv(
        "PROFILE_DECORATION_FALLBACK_IDS",
        "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-101",
    ).split(",")
    if value.strip()
)

REQUEST_TIMEOUT_SECONDS = 15
LINKEDIN_MAX_CONCURRENCY = max(1, int(os.getenv("LINKEDIN_MAX_CONCURRENCY", "3")))
LINKEDIN_USER_AGENT = os.getenv("LINKEDIN_USER_AGENT") or (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)

# The credential-taking dashboard endpoint is local-only unless explicitly
# enabled behind an operator-controlled HTTPS/authentication layer.
ALLOW_REMOTE_UI_LOGIN = os.getenv("ALLOW_REMOTE_UI_LOGIN", "false").lower() in (
    "1",
    "true",
    "yes",
)

# --- Caching ---------------------------------------------------------------
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "1000"))

# --- Batch endpoint ----------------------------------------------------
MAX_BATCH_SIZE = max(1, int(os.getenv("MAX_BATCH_SIZE", "20")))
MAX_BATCH_CONCURRENCY = max(1, int(os.getenv("MAX_BATCH_CONCURRENCY", "5")))


def session_configured() -> bool:
    return bool(LI_AT)

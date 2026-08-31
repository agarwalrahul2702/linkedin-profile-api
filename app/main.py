import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from scripts.linkedin_login import login as direct_linkedin_login

from . import cache, config
from .linkedin_client import (
    LinkedInAuthError,
    LinkedInClient,
    LinkedInError,
    LinkedInNotFoundError,
    LinkedInRateLimitError,
    LinkedInUpstreamError,
    extract_public_identifier,
)
from .models import (
    BatchProfileRequest,
    BatchProfileResponse,
    BatchProfileResultItem,
    ErrorResponse,
    ProfileRequest,
    ProfileResponse,
    SessionHealthResponse,
    SessionLoginRequest,
    SessionLoginResponse,
)
from .parser import parse_profile


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create one pooled LinkedIn client for the app's lifetime instead of
    # opening a fresh connection per request.
    app.state.linkedin_client = (
        LinkedInClient() if config.session_configured() else None
    )
    app.state.linkedin_semaphore = asyncio.Semaphore(config.LINKEDIN_MAX_CONCURRENCY)
    app.state.linkedin_client_lock = asyncio.Lock()
    yield
    if app.state.linkedin_client is not None:
        await app.state.linkedin_client.aclose()


app = FastAPI(
    title="LinkedIn Profile API",
    description=(
        "Accepts a LinkedIn profile URL and returns structured JSON scraped "
        "directly from LinkedIn's internal Voyager API — no browser involved. "
        "POC — see README for setup, auth, and known limitations."
    ),
    version="0.5.0",
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _get_linkedin_client(request: Request) -> LinkedInClient:
    client = request.app.state.linkedin_client
    if client is None:
        raise HTTPException(
            status_code=401,
            detail="No LinkedIn session is configured. Sign in or set LI_AT.",
        )
    return client


def _parse_profile_response(raw: dict) -> tuple[dict, ProfileResponse]:
    parsed = parse_profile(raw)
    if not any(
        parsed.get(field) for field in ("public_identifier", "name", "headline")
    ):
        raise ValueError("No target profile entity was present in the Voyager payload.")
    return parsed, ProfileResponse(**parsed)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post(
    "/api/v1/session/login",
    response_model=SessionLoginResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def create_linkedin_session(
    payload: SessionLoginRequest, request: Request
) -> SessionLoginResponse:
    """Create an in-memory LinkedIn session through direct HTTP, without a browser."""
    client_host = request.client.host if request.client else ""
    if not config.ALLOW_REMOTE_UI_LOGIN and client_host not in {
        "127.0.0.1",
        "::1",
        "localhost",
        "testclient",
    }:
        raise HTTPException(
            status_code=403,
            detail="Dashboard credential login is restricted to localhost.",
        )

    password = payload.password.get_secret_value()
    try:
        li_at, jsessionid = await asyncio.to_thread(
            direct_linkedin_login,
            payload.linkedin_id.strip(),
            password,
            payload.user_agent.strip(),
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    finally:
        password = ""

    new_client = LinkedInClient(
        li_at=li_at,
        jsessionid=jsessionid,
        user_agent=payload.user_agent.strip(),
    )
    health_result = await new_client.check_session_health()
    if not health_result["valid"]:
        await new_client.aclose()
        raise HTTPException(status_code=401, detail=health_result["detail"])

    async with request.app.state.linkedin_client_lock:
        previous_client = request.app.state.linkedin_client
        request.app.state.linkedin_client = new_client
        cache.clear()
    if previous_client is not None:
        await previous_client.aclose()

    return SessionLoginResponse(
        authenticated=True,
        detail="LinkedIn session is active. Credentials were not stored.",
    )


@app.get(
    "/health/linkedin-session",
    response_model=SessionHealthResponse,
)
async def linkedin_session_health(request: Request) -> SessionHealthResponse:
    client = _get_linkedin_client(request)
    result = await client.check_session_health()
    return SessionHealthResponse(**result)


async def _fetch_one_profile(
    client: LinkedInClient,
    linkedin_url: str,
    linkedin_semaphore: asyncio.Semaphore,
) -> BatchProfileResultItem:
    try:
        public_id = extract_public_identifier(linkedin_url)
    except ValueError as exc:
        return BatchProfileResultItem(linkedin_url=linkedin_url, error=str(exc))

    cached = cache.get(public_id)
    if cached is not None:
        return BatchProfileResultItem(
            linkedin_url=linkedin_url, profile=ProfileResponse(**cached), cached=True
        )

    try:
        async with linkedin_semaphore:
            raw = await client.get_raw_profile(public_id)
        parsed, profile = _parse_profile_response(raw)
    except LinkedInError as exc:
        return BatchProfileResultItem(linkedin_url=linkedin_url, error=str(exc))
    except Exception:
        return BatchProfileResultItem(
            linkedin_url=linkedin_url,
            error="LinkedIn returned a profile payload that could not be parsed.",
        )

    cache.set(public_id, parsed)
    return BatchProfileResultItem(linkedin_url=linkedin_url, profile=profile)


@app.post(
    "/api/v1/profile",
    response_model=ProfileResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def get_profile(payload: ProfileRequest, request: Request) -> ProfileResponse:
    try:
        public_id = extract_public_identifier(payload.linkedin_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    cached = cache.get(public_id)
    if cached is not None:
        return ProfileResponse(**cached)

    client = _get_linkedin_client(request)
    try:
        async with request.app.state.linkedin_semaphore:
            raw = await client.get_raw_profile(public_id)
    except LinkedInAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except LinkedInNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except LinkedInRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except LinkedInUpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    try:
        parsed, response = _parse_profile_response(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="LinkedIn returned a profile payload that could not be parsed.",
        ) from exc
    cache.set(public_id, parsed)
    return response


@app.post(
    "/api/v1/profiles",
    response_model=BatchProfileResponse,
)
async def get_profiles_batch(
    payload: BatchProfileRequest, request: Request
) -> BatchProfileResponse:
    if len(payload.linkedin_urls) > config.MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Batch size {len(payload.linkedin_urls)} exceeds max of {config.MAX_BATCH_SIZE}.",
        )

    client = _get_linkedin_client(request)
    semaphore = asyncio.Semaphore(config.MAX_BATCH_CONCURRENCY)

    async def bounded_fetch(url: str) -> BatchProfileResultItem:
        async with semaphore:
            return await _fetch_one_profile(
                client,
                url,
                request.app.state.linkedin_semaphore,
            )

    results = await asyncio.gather(*(bounded_fetch(u) for u in payload.linkedin_urls))
    return BatchProfileResponse(results=list(results))


@app.exception_handler(HTTPException)
def http_exception_handler(request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.__class__.__name__, "detail": exc.detail},
    )

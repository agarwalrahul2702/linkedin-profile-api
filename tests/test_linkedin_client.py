import asyncio

import httpx
import pytest

from app import config
from app.linkedin_client import (
    LinkedInAuthError,
    LinkedInClient,
    LinkedInNotFoundError,
    LinkedInUpstreamError,
    extract_public_identifier,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.linkedin.com/in/jane-doe-123/", "jane-doe-123"),
        ("https://linkedin.com/in/jane_doe?trk=profile", "jane_doe"),
        ("https://in.linkedin.com/in/jane-doe/details/experience/", "jane-doe"),
    ],
)
def test_extract_public_identifier_accepts_linkedin_profiles(url, expected):
    assert extract_public_identifier(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "not a url linkedin.com/in/jane-doe",
        "http://www.linkedin.com/in/jane-doe/",
        "https://evil-linkedin.com/in/jane-doe/",
        "https://www.linkedin.com.evil.test/in/jane-doe/",
        "http://127.0.0.1/?next=linkedin.com/in/jane-doe",
        "https://www.linkedin.com/company/example/",
        "https://user@www.linkedin.com/in/jane-doe/",
        "https://www.linkedin.com/in/%2Fetc%2Fpasswd/",
    ],
)
def test_extract_public_identifier_rejects_unsafe_or_non_profile_urls(url):
    with pytest.raises(ValueError):
        extract_public_identifier(url)


def test_client_calls_voyager_directly_with_required_headers(monkeypatch):
    monkeypatch.setattr(config, "LI_AT", "session-cookie")
    monkeypatch.setattr(config, "JSESSIONID", "ajax:123")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/voyager/api/identity/dash/profiles"
        assert request.url.params["q"] == "memberIdentity"
        assert request.url.params["memberIdentity"] == "jane-doe"
        assert request.url.params["decorationId"] == config.PROFILE_DECORATION_ID
        assert request.headers["csrf-token"] == "ajax:123"
        assert request.headers["x-restli-protocol-version"] == "2.0.0"
        assert "li_at=session-cookie" in request.headers["cookie"]
        assert 'JSESSIONID="ajax:123"' in request.headers["cookie"]
        return httpx.Response(200, json={"data": {}, "included": []})

    async def run_test():
        async with LinkedInClient(transport=httpx.MockTransport(handler)) as client:
            return await client.get_raw_profile("jane-doe")

    assert asyncio.run(run_test()) == {"data": {}, "included": []}


def test_client_bootstraps_jsessionid_when_only_li_at_is_supplied(monkeypatch):
    monkeypatch.setattr(config, "LI_AT", "session-cookie")
    monkeypatch.setattr(config, "JSESSIONID", "")
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/checkpoint/lg/login":
            assert request.headers["cookie"] == ""
            return httpx.Response(
                200,
                headers={
                    "set-cookie": (
                        'JSESSIONID="ajax:bootstrapped"; Path=/; '
                        "Domain=.linkedin.com"
                    )
                },
                text="login",
            )
        assert request.headers["csrf-token"] == "ajax:bootstrapped"
        assert "li_at=session-cookie" in request.headers["cookie"]
        assert 'JSESSIONID="ajax:bootstrapped"' in request.headers["cookie"]
        return httpx.Response(200, json={"elements": []})

    async def run_test():
        async with LinkedInClient(transport=httpx.MockTransport(handler)) as client:
            return await client.get_raw_profile("jane-doe")

    assert asyncio.run(run_test()) == {"elements": []}
    assert paths == [
        "/checkpoint/lg/login",
        "/voyager/api/identity/dash/profiles",
    ]


def test_client_maps_not_found_without_retrying(monkeypatch):
    monkeypatch.setattr(config, "LI_AT", "session-cookie")
    monkeypatch.setattr(config, "JSESSIONID", "ajax:123")
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(404)

    async def run_test():
        async with LinkedInClient(transport=httpx.MockTransport(handler)) as client:
            await client.get_raw_profile("missing")

    with pytest.raises(LinkedInNotFoundError):
        asyncio.run(run_test())
    assert request_count == 1


def test_client_maps_login_redirect_to_auth_error(monkeypatch):
    monkeypatch.setattr(config, "LI_AT", "expired-session")
    monkeypatch.setattr(config, "JSESSIONID", "ajax:123")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": str(request.url)})

    async def run_test():
        async with LinkedInClient(transport=httpx.MockTransport(handler)) as client:
            await client.get_raw_profile("jane-doe")

    with pytest.raises(LinkedInAuthError):
        asyncio.run(run_test())


def test_client_falls_back_when_primary_decoration_is_retired(monkeypatch):
    monkeypatch.setattr(config, "LI_AT", "session-cookie")
    monkeypatch.setattr(config, "JSESSIONID", "ajax:123")
    monkeypatch.setattr(config, "PROFILE_DECORATION_ID", "profile-decoration-109")
    monkeypatch.setattr(
        config, "PROFILE_DECORATION_FALLBACK_IDS", ("profile-decoration-101",)
    )
    attempted = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request.url.params["decorationId"])
        if attempted[-1] == "profile-decoration-109":
            return httpx.Response(410)
        return httpx.Response(200, json={"data": {}, "included": []})

    async def run_test():
        async with LinkedInClient(transport=httpx.MockTransport(handler)) as client:
            return await client.get_raw_profile("jane-doe")

    assert asyncio.run(run_test()) == {"data": {}, "included": []}
    assert attempted == ["profile-decoration-109", "profile-decoration-101"]


def test_client_reports_schema_drift_after_all_decorations_fail(monkeypatch):
    monkeypatch.setattr(config, "LI_AT", "session-cookie")
    monkeypatch.setattr(config, "JSESSIONID", "ajax:123")
    monkeypatch.setattr(config, "PROFILE_DECORATION_ID", "profile-decoration-109")
    monkeypatch.setattr(
        config, "PROFILE_DECORATION_FALLBACK_IDS", ("profile-decoration-101",)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(410)

    async def run_test():
        async with LinkedInClient(transport=httpx.MockTransport(handler)) as client:
            await client.get_raw_profile("jane-doe")

    with pytest.raises(LinkedInUpstreamError, match="schema versions"):
        asyncio.run(run_test())

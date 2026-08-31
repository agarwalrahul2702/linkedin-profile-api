import importlib

from fastapi.testclient import TestClient

from app.linkedin_client import LinkedInUpstreamError
from app.main import app


class FakeLinkedInClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    async def get_raw_profile(self, public_identifier):
        if self.error:
            raise self.error
        return self.payload

    async def check_session_health(self):
        return {"valid": True, "detail": "Session is active."}

    async def aclose(self):
        return None


def test_health_and_invalid_url():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post("/api/v1/profile", json={"linkedin_url": "bad"})

    assert response.status_code == 400
    assert response.json()["error"] == "HTTPException"


def test_dashboard_and_static_assets_are_served():
    with TestClient(app) as client:
        page = client.get("/")
        script = client.get("/static/app.js")

    assert page.status_code == 200
    assert "Profile Lens" in page.text
    assert 'id="login-form"' in page.text
    assert 'id="profile-result"' in page.text
    assert script.status_code == 200
    assert "renderProfile" in script.text


def test_ui_login_replaces_runtime_session_without_persisting_password(monkeypatch):
    main_module = importlib.import_module("app.main")
    captured = {}

    def fake_login(linkedin_id, password, user_agent):
        captured.update(
            linkedin_id=linkedin_id,
            password=password,
            user_agent=user_agent,
        )
        return "new-li-at", "ajax:new"

    class RuntimeClient(FakeLinkedInClient):
        def __init__(self, **kwargs):
            super().__init__()
            captured["client_kwargs"] = kwargs

    monkeypatch.setattr(main_module, "direct_linkedin_login", fake_login)
    monkeypatch.setattr(main_module, "LinkedInClient", RuntimeClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/session/login",
            json={
                "linkedin_id": "person@example.com",
                "password": "one-time-password",
                "user_agent": "Mozilla/5.0 Test Browser",
            },
        )

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert captured["client_kwargs"] == {
        "li_at": "new-li-at",
        "jsessionid": "ajax:new",
        "user_agent": "Mozilla/5.0 Test Browser",
    }


def test_profile_endpoint_parses_linkedin_response(profile_payload):
    with TestClient(app) as client:
        app.state.linkedin_client = FakeLinkedInClient(profile_payload)
        response = client.post(
            "/api/v1/profile",
            json={"linkedin_url": "https://www.linkedin.com/in/jane-doe-123/"},
        )

    assert response.status_code == 200
    assert response.json()["name"] == "Jane Doe"
    assert response.json()["experience"][0]["date_range"] == "06/2022 - Present"


def test_upstream_failure_is_mapped_to_502():
    with TestClient(app) as client:
        app.state.linkedin_client = FakeLinkedInClient(
            error=LinkedInUpstreamError("LinkedIn returned malformed JSON.")
        )
        response = client.post(
            "/api/v1/profile",
            json={"linkedin_url": "https://www.linkedin.com/in/upstream-error/"},
        )

    assert response.status_code == 502
    assert response.json()["detail"] == "LinkedIn returned malformed JSON."


def test_empty_voyager_payload_is_mapped_to_502():
    with TestClient(app) as client:
        app.state.linkedin_client = FakeLinkedInClient({"data": {}, "included": []})
        response = client.post(
            "/api/v1/profile",
            json={"linkedin_url": "https://www.linkedin.com/in/empty-profile/"},
        )

    assert response.status_code == 502
    assert "could not be parsed" in response.json()["detail"]


def test_batch_isolates_invalid_and_upstream_failures(profile_payload):
    class MixedClient(FakeLinkedInClient):
        async def get_raw_profile(self, public_identifier):
            if public_identifier == "fails":
                raise LinkedInUpstreamError("Temporary LinkedIn failure.")
            return profile_payload

    with TestClient(app) as client:
        app.state.linkedin_client = MixedClient()
        response = client.post(
            "/api/v1/profiles",
            json={
                "linkedin_urls": [
                    "https://www.linkedin.com/in/jane-doe-123/",
                    "not-a-linkedin-url",
                    "https://www.linkedin.com/in/fails/",
                ]
            },
        )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["profile"]["name"] == "Jane Doe"
    assert "Expected an HTTPS LinkedIn profile URL" in results[1]["error"]
    assert results[2]["error"] == "Temporary LinkedIn failure."

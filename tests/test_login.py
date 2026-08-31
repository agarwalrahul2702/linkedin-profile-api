from pathlib import Path

from scripts.linkedin_login import LOGIN_URL, parse_login_form, update_env_file


def test_login_uses_server_rendered_checkpoint_form():
    assert LOGIN_URL == "https://www.linkedin.com/checkpoint/lg/login"


def test_parse_login_form_extracts_action_and_hidden_fields():
    html = """
    <form action="/checkpoint/lg/login-submit" method="post">
      <input type="hidden" name="loginCsrfParam" value="csrf-token">
      <input type="hidden" name="pageInstance" value="urn:li:page:test">
      <input name="session_key">
      <input name="session_password">
    </form>
    """

    action, fields = parse_login_form(html)

    assert action == "/checkpoint/lg/login-submit"
    assert fields["loginCsrfParam"] == "csrf-token"
    assert fields["pageInstance"] == "urn:li:page:test"


def test_update_env_file_preserves_other_configuration(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("LI_AT=old\nCACHE_TTL_SECONDS=300\n")

    update_env_file(
        env_path,
        {
            "LI_AT": "new-session",
            "JSESSIONID": "ajax:123",
            "LINKEDIN_USER_AGENT": "test-agent",
        },
    )

    content = env_path.read_text()
    assert "LI_AT=new-session" in content
    assert "JSESSIONID=ajax:123" in content
    assert "LINKEDIN_USER_AGENT=test-agent" in content
    assert "CACHE_TTL_SECONDS=300" in content
    assert env_path.stat().st_mode & 0o777 == 0o600

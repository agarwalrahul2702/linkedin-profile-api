#!/usr/bin/env python3
"""Create LinkedIn session cookies through direct HTTP login (no browser)."""

import argparse
import getpass
import os
import stat
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx


LOGIN_URL = "https://www.linkedin.com/checkpoint/lg/login"
EXPECTED_LOGIN_ACTION = "/checkpoint/lg/login-submit"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)


class LoginFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action: Optional[str] = None
        self.fields: dict[str, str] = {}
        self._inside_login_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            action = attributes.get("action") or ""
            self._inside_login_form = "login-submit" in action
            if self._inside_login_form:
                self.action = action
            return
        if tag != "input" or not self._inside_login_form:
            return
        name = attributes.get("name")
        if name:
            self.fields[name] = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._inside_login_form:
            self._inside_login_form = False


def parse_login_form(html: str) -> tuple[str, dict[str, str]]:
    parser = LoginFormParser()
    parser.feed(html)
    if not parser.action or not parser.fields.get("loginCsrfParam"):
        raise RuntimeError("LinkedIn login form or CSRF token was not found.")
    return parser.action, parser.fields


def _cookie_value(client: httpx.Client, name: str) -> Optional[str]:
    values = [cookie.value for cookie in client.cookies.jar if cookie.name == name]
    return values[-1] if values else None


def update_env_file(path: Path, values: dict[str, str]) -> None:
    existing_lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)
    updated_lines: list[str] = []

    for line in existing_lines:
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in remaining:
            updated_lines.append(f"{key}={remaining.pop(key)}")
        else:
            updated_lines.append(line)

    if remaining and updated_lines and updated_lines[-1]:
        updated_lines.append("")
    updated_lines.extend(f"{key}={value}" for key, value in remaining.items())

    path.write_text("\n".join(updated_lines) + "\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def login(email: str, password: str, user_agent: str) -> tuple[str, str]:
    headers = {
        "user-agent": user_agent,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
    }
    with httpx.Client(headers=headers, timeout=20, follow_redirects=False) as client:
        login_page = client.get(LOGIN_URL)
        login_page.raise_for_status()
        action, fields = parse_login_form(login_page.text)

        fields.update(
            {
                "session_key": email,
                "session_password": password,
                "rememberMeOptIn": "true",
            }
        )
        response = client.post(
            urljoin(LOGIN_URL, action),
            data=fields,
            headers={
                "origin": "https://www.linkedin.com",
                "referer": str(login_page.url),
            },
        )

        location = response.headers.get("location", "")
        location_lower = location.lower()
        if any(
            marker in location_lower
            for marker in ("checkpoint", "challenge", "captcha")
        ):
            raise RuntimeError(
                "LinkedIn requires a checkpoint, MFA, or CAPTCHA. "
                "This direct-HTTP helper will not bypass it."
            )

        li_at = _cookie_value(client, "li_at")
        jsessionid = _cookie_value(client, "JSESSIONID")
        if not li_at or not jsessionid:
            raise RuntimeError(
                "LinkedIn did not issue an authenticated session. "
                f"Login response was HTTP {response.status_code}."
            )

        return li_at.strip('"'), jsessionid.strip('"')


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Log into LinkedIn over direct HTTP and update local session cookies."
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()

    email = input("LinkedIn email: ").strip()
    password = getpass.getpass("LinkedIn password: ")
    if not email or not password:
        print("Email and password are required.")
        return 2

    user_agent = os.getenv("LINKEDIN_USER_AGENT") or DEFAULT_USER_AGENT
    try:
        li_at, jsessionid = login(email, password, user_agent)
        update_env_file(
            args.env_file,
            {
                "LI_AT": li_at,
                "JSESSIONID": jsessionid,
                "LINKEDIN_USER_AGENT": user_agent,
            },
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"Login failed: {exc}")
        return 1
    finally:
        password = ""

    print(f"Session cookies saved to {args.env_file} (permissions: owner read/write).")
    print("Restart the API, then call GET /health/linkedin-session once.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

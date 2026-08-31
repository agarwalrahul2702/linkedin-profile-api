import json
from pathlib import Path

import pytest

from app import cache


@pytest.fixture(autouse=True)
def clear_profile_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def profile_payload() -> dict:
    fixture_path = Path(__file__).parent / "fixtures" / "profile_view.json"
    return json.loads(fixture_path.read_text())


@pytest.fixture
def dash_profile_payload() -> dict:
    fixture_path = Path(__file__).parent / "fixtures" / "dash_profile.json"
    return json.loads(fixture_path.read_text())

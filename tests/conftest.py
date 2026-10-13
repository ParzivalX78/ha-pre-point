"""Shared fixtures. All data here is synthetic; sign-in is never exercised."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.pre_point.models import Location

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def mock_api():
    """Replace the PRE Point API client (and with it auth.py) by a mock."""
    api = MagicMock()
    api.async_get_sessions = AsyncMock(return_value=load("sessions"))
    api.async_get_access_token = AsyncMock(return_value="not-a-real-token")
    api.async_close = AsyncMock()
    api.async_forget_stored_tokens = AsyncMock()
    with (
        patch("custom_components.pre_point.PREPointApi", return_value=api) as setup_cls,
        patch("custom_components.pre_point.config_flow.PREPointApi", return_value=api) as flow_cls,
    ):
        api.setup_cls, api.flow_cls = setup_cls, flow_cls
        yield api


@pytest.fixture
def mock_poi():
    """Replace the public charger map client by a mock."""
    poi = MagicMock()
    location = load("location")
    poi.locations = AsyncMock(side_effect=lambda ids: [Location.from_api(location)])
    poi.evse_status = AsyncMock(return_value={})
    poi.locations_for_evses = AsyncMock(side_effect=lambda ids: [Location.from_api(location)])
    poi.locations_near = AsyncMock(return_value=[])
    with (
        patch("custom_components.pre_point.PoiClient", return_value=poi),
        patch("custom_components.pre_point.config_flow.PoiClient", return_value=poi),
    ):
        yield poi

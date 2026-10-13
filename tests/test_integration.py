"""Setup, entities, statistics and flows with mocked clients (no sign-in, no network)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    statistic_during_period,
    statistics_during_period,
)
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.pre_point.api import PreAuthError, PreInvalidCredentialsError
from custom_components.pre_point.const import DOMAIN
from custom_components.pre_point.statistics import STAT_COST, STAT_ENERGY

from .conftest import load

ENTRY_DATA = {"username": "driver@example.com", "password": "not-a-real-password"}
OPTIONS = {
    "price_ac": 9.0,
    "price_dc": 12.0,
    "price_ufc": 14.0,
    "monthly_fee": 0.0,
    "locations": ["CZT0001"],
    "scan_interval": 600,
}


@pytest.fixture
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Prepare the recorder database before hass starts (statistics tests)."""


async def _setup(hass: HomeAssistant, options=OPTIONS) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, options=dict(options), unique_id="driver@example.com"
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _state(hass: HomeAssistant, entry: MockConfigEntry, unique_suffix: str):
    registry = er.async_get(hass)
    entity_id = next(
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id == f"{entry.entry_id}_{unique_suffix}"
    )
    return hass.states.get(entity_id)


@pytest.fixture
async def prague(hass: HomeAssistant, freezer: FrozenDateTimeFactory):
    await hass.config.async_set_time_zone("Europe/Prague")
    freezer.move_to("2026-10-13 10:00:00+00:00")
    return freezer


async def test_entities(hass: HomeAssistant, mock_api, mock_poi, prague) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    last = _state(hass, entry, "latest")
    assert last.state == "18.453"
    assert last.attributes["tariff"] == "ufc"
    assert last.attributes["price_per_kwh"] == 14.0
    assert "api_response" not in last.attributes
    assert _state(hass, entry, "last_session_cost").state == "258.34"
    assert _state(hass, entry, "month_energy").state == "28.717"
    assert _state(hass, entry, "month_cost").state == "381.51"
    assert _state(hass, entry, "sessions").state == "3"

    assert _state(hass, entry, "location_CZT0001_available").state == "1"
    dc = _state(hass, entry, "evse_CZ*PRE*E1T0001*3_status")
    assert dc.state == "out_of_order"
    assert dc.attributes["tariff"] == "ufc"
    assert dc.attributes["price_per_kwh"] == 14.0

    # Later cycles only ask for the status, in one request.
    mock_poi.evse_status.return_value = {"CZ*PRE*E1T0001*3": "AVAILABLE"}
    await entry.runtime_data.stations.async_refresh()
    await hass.async_block_till_done()
    mock_poi.evse_status.assert_awaited_with(
        ["CZ*PRE*E1T0001*1", "CZ*PRE*E1T0001*2", "CZ*PRE*E1T0001*3"]
    )
    assert _state(hass, entry, "location_CZT0001_available").state == "2"

    # The token provider is reachable for other modules.
    assert await entry.runtime_data.api.async_get_access_token() == "not-a-real-token"


async def test_prices_are_kept_unless_recalculated(
    hass: HomeAssistant, mock_api, mock_poi, prague
) -> None:
    entry = await _setup(hass)
    assert _state(hass, entry, "last_session_cost").state == "258.34"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    new = {**OPTIONS, "price_ufc": 10.0, "radius": 15.0, "query": ""}
    result = await hass.config_entries.options.async_configure(result["flow_id"], new)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["price_ufc"] == 10.0
    # Already priced session keeps the old price ...
    assert _state(hass, entry, "last_session_cost").state == "258.34"

    # ... until a recalculation is asked for.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**new, "recalculate": True}
    )
    await hass.async_block_till_done()
    assert _state(hass, entry, "last_session_cost").state == "184.53"


async def test_statistics(
    recorder_mock, hass: HomeAssistant, mock_api, mock_poi, prague
) -> None:
    entry = await _setup(hass, {**OPTIONS, "monthly_fee": 50.0})
    await async_wait_recording_done(hass)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 8, 31, 22, tzinfo=dt_util.UTC),
        None,
        {STAT_ENERGY, STAT_COST},
        "month",
        None,
        {"sum", "change"},
    )
    assert [(r["sum"], r["change"]) for r in stats[STAT_ENERGY]] == [
        (24.155, 24.155),
        (52.872, 28.717),
    ]
    # September: session + fee, October: two sessions + fee.
    assert [(r["sum"], r["change"]) for r in stats[STAT_COST]] == [
        (267.4, 267.4),
        (698.91, 431.51),
    ]
    assert _state(hass, entry, "month_cost").state == "431.51"

    # "This month" as the statistic card asks for it (needs the daily rows).
    october = datetime(2026, 9, 30, 22, tzinfo=dt_util.UTC)
    for statistic_id, expected in ((STAT_ENERGY, 28.717), (STAT_COST, 431.51)):
        result = await get_instance(hass).async_add_executor_job(
            statistic_during_period, hass, october, None, statistic_id, {"change"}, None
        )
        assert result["change"] == expected


async def test_rejected_password_asks_for_reauth(hass: HomeAssistant, mock_api, mock_poi) -> None:
    mock_api.async_get_sessions.side_effect = PreAuthError("locked", reauth_required=True)
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flows and flows[0]["step_id"] == "reauth_confirm"

    mock_api.async_get_sessions.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {"password": "new-password"}
    )
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data["password"] == "new-password"
    assert entry.state is ConfigEntryState.LOADED


async def test_temporary_sign_in_problem_keeps_entry_loaded(
    hass: HomeAssistant, mock_api, mock_poi
) -> None:
    mock_api.async_get_sessions.side_effect = PreAuthError("try later")
    entry = await _setup(hass)
    # Loaded, so auth.py keeps its retry timer; stations work meanwhile.
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.history.update_interval == timedelta(minutes=15)
    assert _state(hass, entry, "location_CZT0001_available").state == "1"
    mock_api.async_close.assert_not_awaited()

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    mock_api.async_close.assert_awaited()


async def test_config_flow(hass: HomeAssistant, mock_api, mock_poi) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["step_id"] == "user"

    mock_api.async_get_sessions.side_effect = PreInvalidCredentialsError()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["errors"] == {"base": "invalid_auth"}

    mock_api.async_get_sessions.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["step_id"] == "tariff"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"price_ac": 9.0, "price_dc": 12.0, "price_ufc": 14.0, "monthly_fee": 0.0},
    )
    assert result["step_id"] == "locations"
    # Places from the history are offered (and pre-selected).
    mock_poi.locations_for_evses.assert_awaited_once()
    assert sorted(mock_poi.locations_for_evses.await_args.args[0]) == [
        "CZ*PRE*E1T0001*1", "CZ*PRE*E1T0002*1", "CZ*PRE*E1T0003*2",
    ]

    # Another radius means a new search, not saving.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"locations": ["CZT0001"], "radius": 30.0, "query": ""}
    )
    assert result["step_id"] == "locations"
    assert mock_poi.locations_near.await_args.args[2] == 30.0
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"locations": ["CZT0001"], "radius": 30.0, "query": ""}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == ENTRY_DATA
    # The sign-in test runs before the entry exists: nothing may be persisted.
    assert all(call.args[1] is None for call in mock_api.flow_cls.call_args_list)
    assert result["options"]["locations"] == ["CZT0001"]
    assert result["options"]["price_ufc"] == 14.0
    await hass.async_block_till_done()


async def test_sessions_survive_in_archive(
    hass: HomeAssistant, hass_storage, mock_api, mock_poi, prague
) -> None:
    """Sessions older than the API window stay in the statistics and sensors."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, options=OPTIONS, unique_id="driver@example.com"
    )
    entry.add_to_hass(hass)
    old = {
        "id": "OLD", "started_at": "2025-01-10T10:00:00+00:00", "energy_kwh": 10.0,
        "duration_s": 3600, "evse_id": "X", "address": None, "city": None,
        "current_type": "AC", "max_power_kw": 22.0, "operator": "CZ*PRE",
        "band": "ac", "price_per_kwh": 8.0, "cost": 80.0,
    }
    hass_storage[f"{DOMAIN}.{entry.entry_id}.sessions"] = {
        "version": 1, "minor_version": 1, "key": f"{DOMAIN}.{entry.entry_id}.sessions",
        "data": {"items": {"OLD": old}, "statistics_fee": 0.0},
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, entry, "sessions").state == "4"


async def test_removal_forgets_stored_token(hass: HomeAssistant, mock_api, mock_poi) -> None:
    entry = await _setup(hass)
    assert mock_api.setup_cls.call_args.args[1] == entry.entry_id
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    mock_api.async_forget_stored_tokens.assert_awaited_once()


async def test_token_store_only_with_entry_id(hass: HomeAssistant) -> None:
    """auth.py: no Store without an entry id (only the constructor is used here)."""
    from custom_components.pre_point.auth import PREPointAuth

    assert PREPointAuth(hass, None, "u@example.com", "x")._token_store is None
    store = PREPointAuth(hass, "abc", "u@example.com", "x")._token_store
    assert store is not None and store.key == "pre_point_abc_auth"

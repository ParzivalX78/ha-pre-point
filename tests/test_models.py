"""Parsing and pricing tests against payloads shaped like the real API."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from custom_components.pre_point.models import (
    ChargingSession,
    Location,
    Tariff,
    hourly_totals,
    month_starts,
    normalize_status,
    summarize_month,
)

from .conftest import load

PRG = ZoneInfo("Europe/Prague")
TARIFF = Tariff(ac=9.0, dc=12.0, ufc=14.0)


def _sessions() -> list[ChargingSession]:
    return [ChargingSession.from_api(i).priced(TARIFF) for i in load("sessions")]


def test_session_from_history_item():
    s = ChargingSession.from_api(load("sessions")[0])
    assert s.id == "CDR-AC-SEP"
    assert s.started_at == datetime(2026, 9, 25, 17, 10, 27, tzinfo=UTC)
    assert s.energy_kwh == 24.155
    assert s.duration_s == 7712
    assert s.evse_id == "CZ*PRE*E1T0001*1"
    assert (s.current_type, s.max_power_kw) == ("AC", 22.0)


def test_session_falls_back_to_charge_code():
    item = load("sessions")[2]
    item["event"]["attributes"] = [
        a for a in item["event"]["attributes"] if a["name"] not in ("cpType", "cpMaxSockPwr")
    ]
    s = ChargingSession.from_api(item)
    assert (s.current_type, s.max_power_kw) == ("DC", 150.0)


def test_tariff_bands_and_prices():
    assert Tariff.band("AC", 22) == "ac"
    assert Tariff.band("DC", 50) == "dc"
    assert Tariff.band("DC", 149) == "dc"
    assert Tariff.band("DC", 150) == "ufc"
    assert Tariff.band(None, None) == "ac"
    ac, dc, ufc = _sessions()
    assert (ac.band, ac.price_per_kwh, ac.cost) == ("ac", 9.0, 217.4)
    assert (dc.band, dc.cost) == ("dc", 123.17)
    assert (ufc.band, ufc.cost) == ("ufc", 258.34)


def test_store_round_trip():
    s = _sessions()[0]
    assert ChargingSession.from_store(s.to_store()) == s


def test_month_summary_and_hourly_totals():
    sessions = _sessions()
    assert summarize_month(sessions, datetime(2026, 10, 13, 12, tzinfo=PRG)) == (28.717, 381.51, 2)
    fee = (datetime(2026, 10, 1, tzinfo=PRG), 100.0)
    rows = hourly_totals(sessions, [fee])
    assert rows[0] == (datetime(2026, 9, 25, 17, tzinfo=UTC), 24.155, 217.4)
    assert rows[1] == (datetime(2026, 9, 30, 22, tzinfo=UTC), 0.0, 100.0)
    assert len(rows) == 4


def test_month_starts_cross_the_year():
    starts = month_starts(datetime(2026, 11, 20, 8, tzinfo=PRG), datetime(2027, 2, 3, tzinfo=PRG))
    assert [(d.year, d.month, d.day, d.hour) for d in starts] == [
        (2026, 11, 1, 0), (2026, 12, 1, 0), (2027, 1, 1, 0), (2027, 2, 1, 0),
    ]


def test_location_and_status():
    loc = Location.from_api(load("location"))
    assert loc.name == "Test Hub"
    assert (loc.latitude, loc.longitude) == (50.1, 14.1)
    assert [e.status for e in loc.evses] == ["available", "charging", "out_of_order"]
    assert loc.available_count == 1
    assert loc.evses[2].label == "DC 150 kW"
    assert loc.evses[2].number == "3"
    assert normalize_status("INOPERATIVE") == "out_of_order"
    assert normalize_status("PLANNED") == "unavailable"
    assert normalize_status(None) == "unknown"

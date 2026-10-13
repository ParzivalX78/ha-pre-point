"""Public charger map client tests with a fake aiohttp session."""

from __future__ import annotations

import pytest

from custom_components.pre_point.const import POI_BASE, POI_KEY_PAGE
from custom_components.pre_point.poi import PoiClient, PoiError

PAGE = "<script>window.poiServiceApiKey = 'key-{n}';</script>"


class _Resp:
    def __init__(self, status, data=None, text=""):
        self.status, self._data, self._text = status, data, text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._data

    async def text(self):
        return self._text


class _Session:
    def __init__(self, posts):
        self.posts = list(posts)
        self.calls = []
        self.pages = 0

    def get(self, url, **kw):
        assert url == POI_KEY_PAGE
        self.pages += 1
        return _Resp(200, text=PAGE.format(n=self.pages))

    def post(self, url, **kw):
        self.calls.append((url, kw["json"], kw["headers"]["Authorization"]))
        return _Resp(*self.posts.pop(0))


async def test_status_uses_key_from_page():
    session = _Session([(200, [{"evse_id": "A*1", "status": "AVAILABLE"}, {"status": "X"}])])
    poi = PoiClient(session)
    assert await poi.evse_status(["A*1"]) == {"A*1": "AVAILABLE"}
    assert session.calls == [(f"{POI_BASE}/b2c/evses/status", {"evse_ids": ["A*1"]}, "Bearer key-1")]


async def test_rotated_key_is_read_again_once():
    session = _Session([(401, None), (200, [])])
    poi = PoiClient(session)
    assert await poi.evse_status(["A*1"]) == {}
    assert [c[2] for c in session.calls] == ["Bearer key-1", "Bearer key-2"]


async def test_errors_are_reported():
    poi = PoiClient(_Session([(500, None)]))
    with pytest.raises(PoiError):
        await poi.locations(["L1"])


async def test_no_ids_no_request():
    session = _Session([])
    poi = PoiClient(session)
    assert await poi.evse_status([]) == {}
    assert await poi.locations([]) == []
    assert session.pages == 0

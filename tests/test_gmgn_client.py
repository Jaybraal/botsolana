import time

import httpx
import pytest

import copytrade.gmgn_client as gc


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "CACHE_DIR", str(tmp_path))


def test_request_sin_api_key_devuelve_none(monkeypatch):
    monkeypatch.setattr(gc, "GMGN_API_KEY", "")
    assert gc._request("/wallet/stats", {"address": "ABC"}) is None


def test_request_ok_devuelve_json(monkeypatch):
    monkeypatch.setattr(gc, "GMGN_API_KEY", "testkey")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"win_rate": 61.5}

    def fake_get(url, params, headers, timeout):
        assert headers["Authorization"] == "Bearer testkey"
        return FakeResponse()

    monkeypatch.setattr(gc.httpx, "get", fake_get)
    assert gc._request("/wallet/stats", {"address": "ABC"}) == {"win_rate": 61.5}


def test_request_error_de_red_devuelve_none(monkeypatch):
    monkeypatch.setattr(gc, "GMGN_API_KEY", "testkey")

    def fake_get(*a, **kw):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(gc.httpx, "get", fake_get)
    assert gc._request("/wallet/stats", {"address": "ABC"}) is None


def test_get_wallet_stats_usa_cache_si_esta_fresca(monkeypatch):
    calls = []

    def fake_request(path, params):
        calls.append(1)
        return {"win_rate": 70.0}

    monkeypatch.setattr(gc, "_request", fake_request)
    r1 = gc.get_wallet_stats("ABC")
    r2 = gc.get_wallet_stats("ABC")
    assert r1 == {"win_rate": 70.0}
    assert r2 == {"win_rate": 70.0}
    assert len(calls) == 1


def test_get_wallet_stats_cache_expirada_reconsulta(monkeypatch):
    monkeypatch.setattr(gc, "CACHE_TTL_SECONDS", 0)
    calls = []

    def fake_request(path, params):
        calls.append(1)
        return {"win_rate": 70.0}

    monkeypatch.setattr(gc, "_request", fake_request)
    gc.get_wallet_stats("ABC")
    time.sleep(0.01)
    gc.get_wallet_stats("ABC")
    assert len(calls) == 2


def test_get_wallet_stats_fallback_a_cache_expirada_si_falla_red(monkeypatch):
    monkeypatch.setattr(gc, "CACHE_TTL_SECONDS", 0)
    responses = [{"win_rate": 70.0}, None]

    def fake_request(path, params):
        return responses.pop(0)

    monkeypatch.setattr(gc, "_request", fake_request)
    r1 = gc.get_wallet_stats("ABC")
    time.sleep(0.01)
    r2 = gc.get_wallet_stats("ABC")
    assert r1 == {"win_rate": 70.0}
    assert r2 == {"win_rate": 70.0}


def test_get_wallet_stats_sin_cache_ni_red_devuelve_none(monkeypatch):
    monkeypatch.setattr(gc, "_request", lambda path, params: None)
    assert gc.get_wallet_stats("NUEVA") is None


def test_get_smart_money_wallets_devuelve_lista(monkeypatch):
    monkeypatch.setattr(gc, "_request", lambda path, params: [{"address": "X", "win_rate": 80.0}])
    result = gc.get_smart_money_wallets(limit=10)
    assert result == [{"address": "X", "win_rate": 80.0}]


def test_get_smart_money_wallets_sin_cache_ni_red_devuelve_lista_vacia(monkeypatch):
    monkeypatch.setattr(gc, "_request", lambda path, params: None)
    assert gc.get_smart_money_wallets() == []

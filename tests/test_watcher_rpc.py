"""
Tests para fetch_transaction_async: debe manejar 429 (cuota Helius agotada)
sin reventar con JSONDecodeError, y usar el RPC fallback antes de rendirse.

Bug real reproducido 06-07/08/26: Helius devuelve 429 con body de texto plano
("max usage reached"), r.json() explota con "Expecting value: line 1 column 1
(char 0)", se traga como excepción genérica y la tx se pierde — sin fallback.
"""
import asyncio

import copytrade.watcher as watcher


class _FakeResp:
    """Simula httpx.Response: JSON válido, o 429 con body de texto plano."""

    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text if text else (str(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._body


def test_fetch_transaction_async_ok_devuelve_result(monkeypatch):
    calls = []

    class _FakeClient:
        async def post(self, url, json=None):
            calls.append(url)
            return _FakeResp(200, {"jsonrpc": "2.0", "id": 1, "result": {"slot": 123}})

    monkeypatch.setattr(watcher, "_rpc_client", _FakeClient())

    result = asyncio.run(watcher.fetch_transaction_async("sig123"))

    assert result == {"slot": 123}
    assert calls == [watcher.RPC_HTTP]


def test_fetch_transaction_async_429_usa_fallback(monkeypatch):
    """El bug real: Helius agota cuota (429, body no-JSON) -> debe reintentar en el fallback."""
    calls = []

    class _FakeClient:
        async def post(self, url, json=None):
            calls.append(url)
            if url == watcher.RPC_HTTP:
                return _FakeResp(429, text="max usage reached")
            return _FakeResp(200, {"jsonrpc": "2.0", "id": 1, "result": {"slot": 999}})

    monkeypatch.setattr(watcher, "_rpc_client", _FakeClient())

    result = asyncio.run(watcher.fetch_transaction_async("sig456"))

    assert result == {"slot": 999}
    assert calls == [watcher.RPC_HTTP, watcher.RPC_HTTP_FALLBACK]


def test_fetch_transaction_async_429_en_ambos_retorna_none_sin_excepcion(monkeypatch):
    class _FakeClient:
        async def post(self, url, json=None):
            return _FakeResp(429, text="max usage reached")

    monkeypatch.setattr(watcher, "_rpc_client", _FakeClient())

    result = asyncio.run(watcher.fetch_transaction_async("sig789"))

    assert result is None

import asyncio

import pytest

import copytrade.learner_scanner as ls


@pytest.fixture(autouse=True)
def _aislar_estado(monkeypatch):
    ls._auto_positions.clear()
    monkeypatch.setattr(ls, "_get_sol_price", lambda: 150.0)

    async def _fake_monitor(mint, symbol):
        return None

    monkeypatch.setattr(ls, "_monitor_position", _fake_monitor)
    yield
    ls._auto_positions.clear()


def _token(price=1.0):
    return {
        "price_usd": price,
        "symbol": "TST",
        "program": "PumpSwap",
        "pair_address": "PAIRX",
    }


def test_usa_precio_fresco_como_entrada(monkeypatch):
    compras = []

    async def _fake_exec(swap):
        compras.append(swap)
        return True

    monkeypatch.setattr(ls, "execute_copy", _fake_exec)
    monkeypatch.setattr(ls, "_fetch_current_price", lambda m, p="": 1.05)

    asyncio.run(ls._open_position("MINT1", _token(1.0), "test"))

    assert "MINT1" in ls._auto_positions
    assert ls._auto_positions["MINT1"]["entry_price_usd"] == pytest.approx(1.05)
    assert len(compras) == 1


def test_aborta_si_precio_se_movio_mas_del_limite(monkeypatch):
    async def _fake_exec(swap):
        raise AssertionError("no debe comprar con precio movido >10%")

    monkeypatch.setattr(ls, "execute_copy", _fake_exec)
    monkeypatch.setattr(ls, "_fetch_current_price", lambda m, p="": 1.5)  # +50%

    asyncio.run(ls._open_position("MINT2", _token(1.0), "test"))

    assert "MINT2" not in ls._auto_positions


def test_aborta_si_no_hay_precio_fresco(monkeypatch):
    async def _fake_exec(swap):
        raise AssertionError("no debe comprar sin precio")

    monkeypatch.setattr(ls, "execute_copy", _fake_exec)
    monkeypatch.setattr(ls, "_fetch_current_price", lambda m, p="": 0.0)

    asyncio.run(ls._open_position("MINT3", _token(1.0), "test"))

    assert "MINT3" not in ls._auto_positions

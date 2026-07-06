import asyncio

import pytest

import copytrade.learner_scanner as ls


@pytest.fixture(autouse=True)
def _aislar_estado(monkeypatch):
    ls._auto_positions.clear()
    ls._monitor_tasks.clear()

    async def _fake_monitor(mint, symbol):
        await asyncio.sleep(3600)  # simula monitor vivo

    monkeypatch.setattr(ls, "_monitor_position", _fake_monitor)
    yield
    for t in ls._monitor_tasks.values():
        t.cancel()
    ls._monitor_tasks.clear()
    ls._auto_positions.clear()


def test_ensure_monitor_lanza_y_no_duplica():
    async def run():
        assert ls._ensure_monitor("M1", "TST") is True
        assert ls._ensure_monitor("M1", "TST") is False  # ya hay uno vivo

    asyncio.run(run())


def test_ensure_monitor_relanza_si_murio():
    async def run():
        assert ls._ensure_monitor("M2", "TST") is True
        ls._monitor_tasks["M2"].cancel()
        await asyncio.sleep(0.05)  # deja que la cancelación se propague y la task quede done()
        assert ls._ensure_monitor("M2", "TST") is True  # relanzado

    asyncio.run(run())

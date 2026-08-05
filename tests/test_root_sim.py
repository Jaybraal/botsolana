import json
import threading

import pytest

import copytrade.root_sim as rsim

ENTRY_CONTEXT = {"price_usd": 1.0e-05, "mcap_usd": 12000, "liquidity_usd": 5000}


@pytest.fixture(autouse=True)
def isolate_root_sim(tmp_path, monkeypatch):
    """Cada test usa su propio balance/history — nunca toca los del repo real
    ni comparte posiciones abiertas entre tests."""
    monkeypatch.setattr(rsim, "BALANCE_PATH", str(tmp_path / "root_sim_balance.json"))
    monkeypatch.setattr(rsim, "HISTORY_PATH", str(tmp_path / "root_sim_history.json"))
    rsim._positions.clear()
    yield
    rsim._positions.clear()


# ── _decide_exit: función pura ──────────────────────────────────────────────

def test_decide_exit_sin_precio_no_decide():
    assert rsim._decide_exit(1.0, None, elapsed_min=1, stop_loss_pct=15, max_hold_min=30) is None
    assert rsim._decide_exit(1.0, 0, elapsed_min=1, stop_loss_pct=15, max_hold_min=30) is None


def test_decide_exit_stop_loss():
    # cae 20% desde 1.0 → 0.80, con stop_loss_pct=15 debe cerrar
    out = rsim._decide_exit(1.0, 0.80, elapsed_min=2, stop_loss_pct=15, max_hold_min=30)
    assert out["reason"] == "stop_loss"
    assert out["pnl_pct"] == pytest.approx(-20.0)


def test_decide_exit_max_hold():
    # sube 5% pero ya pasó el tiempo máximo → cierre por tiempo
    out = rsim._decide_exit(1.0, 1.05, elapsed_min=31, stop_loss_pct=15, max_hold_min=30)
    assert out["reason"] == "max_hold"
    assert out["pnl_pct"] == pytest.approx(5.0)


def test_decide_exit_sigue_abierta():
    out = rsim._decide_exit(1.0, 1.02, elapsed_min=5, stop_loss_pct=15, max_hold_min=30)
    assert out is None


# ── _close_position: persistencia ───────────────────────────────────────────

def test_close_position_actualiza_balance_y_history(tmp_path):
    position = {
        "wallet": "Cented", "token_mint": "ABC123", "entry_price": 1.0,
        "amount_usd": 50.0, "opened_at": 1000.0, "root_score": 72, "root_prob": 0.72,
        "entry_context": ENTRY_CONTEXT,
    }
    exit_info = {"reason": "max_hold", "pnl_pct": 10.0}

    rsim._close_position(position, exit_info, current_price=1.10)

    balance = json.loads((tmp_path / "root_sim_balance.json").read_text())
    assert balance["balance"] == pytest.approx(rsim.INITIAL_BALANCE + 5.0)  # 50 * 10% = $5

    history = [json.loads(l) for l in (tmp_path / "root_sim_history.json").read_text().strip().splitlines()]
    assert len(history) == 1
    assert history[0]["wallet"] == "Cented"
    assert history[0]["pnl_pct"] == 10.0
    assert history[0]["pnl_usd"] == pytest.approx(5.0)
    assert history[0]["exit_reason"] == "max_hold"
    assert history[0]["won"] is True
    # Features guardadas junto al resultado — para poder reentrenar sobre los
    # propios trades de ROOT más adelante (label = won/pnl_pct).
    assert history[0]["entry_context"] == ENTRY_CONTEXT


def test_close_position_perdida_marca_won_false(tmp_path):
    position = {
        "wallet": "Theo", "token_mint": "XYZ", "entry_price": 1.0,
        "amount_usd": 50.0, "opened_at": 1000.0, "root_score": 60, "root_prob": 0.6,
    }
    exit_info = {"reason": "stop_loss", "pnl_pct": -15.0}

    rsim._close_position(position, exit_info, current_price=0.85)

    history = [json.loads(l) for l in (tmp_path / "root_sim_history.json").read_text().strip().splitlines()]
    assert history[0]["won"] is False
    assert history[0]["pnl_usd"] == pytest.approx(-7.5)


def test_close_position_dos_trades_acumulan_balance(tmp_path):
    pos = {"wallet": "A", "token_mint": "T1", "entry_price": 1.0, "amount_usd": 50.0, "opened_at": 0, "root_score": 1, "root_prob": 0.5}
    rsim._close_position(pos, {"reason": "max_hold", "pnl_pct": 10.0}, 1.1)
    pos2 = {"wallet": "B", "token_mint": "T2", "entry_price": 1.0, "amount_usd": 50.0, "opened_at": 0, "root_score": 1, "root_prob": 0.5}
    rsim._close_position(pos2, {"reason": "max_hold", "pnl_pct": -10.0}, 0.9)

    balance = json.loads((tmp_path / "root_sim_balance.json").read_text())
    assert balance["balance"] == pytest.approx(rsim.INITIAL_BALANCE)  # +5 -5 = neto 0

    history = (tmp_path / "root_sim_history.json").read_text().strip().splitlines()
    assert len(history) == 2


# ── open_position: gating (sin lanzar hilos reales) ─────────────────────────

def test_open_position_no_abre_sin_entry_price(monkeypatch):
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: pytest.fail("no debería crear Thread"))
    rsim.open_position("Cented", "MINT1", {}, root_score=80, root_prob=0.8)
    assert "MINT1" not in rsim._positions


def test_open_position_no_abre_sin_pair_address(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: None)
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: pytest.fail("no debería crear Thread"))
    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8)
    assert "MINT1" not in rsim._positions


def test_open_position_no_abre_dos_veces_el_mismo_token(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    calls = []
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: calls.append(1) or _FakeThread())

    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8)
    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8)

    assert len(calls) == 1  # la segunda llamada no debió lanzar otro hilo


def test_open_position_no_abre_si_balance_agotado(tmp_path, monkeypatch):
    (tmp_path / "root_sim_balance.json").write_text(json.dumps({"balance": 0, "initial": rsim.INITIAL_BALANCE}))
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: pytest.fail("no debería crear Thread"))
    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8)
    assert "MINT1" not in rsim._positions


def test_open_position_abre_y_registra_posicion(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: _FakeThread())

    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8)

    assert "MINT1" in rsim._positions
    pos = rsim._positions["MINT1"]
    assert pos["wallet"] == "Cented"
    assert pos["entry_price"] == ENTRY_CONTEXT["price_usd"]
    assert pos["pair_address"] == "PAIR1"
    assert pos["amount_usd"] == rsim.TRADE_USD


class _FakeThread:
    def start(self):
        pass

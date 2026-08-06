import json
import threading

import pytest

import copytrade.root_sim as rsim
import copytrade.root_trajectory as traj

ENTRY_CONTEXT = {"price_usd": 1.0e-05, "mcap_usd": 12000, "liquidity_usd": 5000}


@pytest.fixture(autouse=True)
def isolate_root_sim(tmp_path, monkeypatch):
    monkeypatch.setattr(rsim, "BALANCE_PATH", str(tmp_path / "root_sim_balance.json"))
    monkeypatch.setattr(rsim, "HISTORY_PATH", str(tmp_path / "root_sim_history.json"))
    monkeypatch.setattr(traj, "TRAJECTORIES_PATH", str(tmp_path / "root_trajectories.jsonl"))
    rsim._positions.clear()
    traj._open_trajectories.clear()
    yield
    rsim._positions.clear()
    traj._open_trajectories.clear()


# ── _decide_exit: función pura (sin cambios) ────────────────────────────────

def test_decide_exit_sin_precio_no_decide():
    assert rsim._decide_exit(1.0, None, elapsed_min=1, stop_loss_pct=15, max_hold_min=30) is None
    assert rsim._decide_exit(1.0, 0, elapsed_min=1, stop_loss_pct=15, max_hold_min=30) is None


def test_decide_exit_stop_loss():
    out = rsim._decide_exit(1.0, 0.80, elapsed_min=2, stop_loss_pct=15, max_hold_min=30)
    assert out["reason"] == "stop_loss"
    assert out["pnl_pct"] == pytest.approx(-20.0)


def test_decide_exit_max_hold():
    out = rsim._decide_exit(1.0, 1.05, elapsed_min=31, stop_loss_pct=15, max_hold_min=30)
    assert out["reason"] == "max_hold"
    assert out["pnl_pct"] == pytest.approx(5.0)


def test_decide_exit_sigue_abierta():
    out = rsim._decide_exit(1.0, 1.02, elapsed_min=5, stop_loss_pct=15, max_hold_min=30)
    assert out is None


# ── _close_position: persistencia parametrizada por path ───────────────────

def test_close_position_actualiza_balance_y_history(tmp_path):
    balance_path = str(tmp_path / "root_sim_balance.json")
    history_path = str(tmp_path / "root_sim_history.json")
    position = {
        "wallet": "Cented", "token_mint": "ABC123", "config_id": "champion", "entry_price": 1.0,
        "amount_usd": 50.0, "opened_at": 1000.0, "root_score": 72, "root_prob": 0.72,
        "entry_context": ENTRY_CONTEXT, "balance_path": balance_path, "history_path": history_path,
    }
    exit_info = {"reason": "max_hold", "pnl_pct": 10.0}

    rsim._close_position(position, exit_info, current_price=1.10)

    balance = json.loads(open(balance_path).read())
    assert balance["balance"] == pytest.approx(rsim.INITIAL_BALANCE + 5.0)

    history = [json.loads(l) for l in open(history_path).read().strip().splitlines()]
    assert len(history) == 1
    assert history[0]["wallet"] == "Cented"
    assert history[0]["config_id"] == "champion"
    assert history[0]["pnl_pct"] == 10.0
    assert history[0]["pnl_usd"] == pytest.approx(5.0)
    assert history[0]["exit_reason"] == "max_hold"
    assert history[0]["won"] is True
    assert history[0]["entry_context"] == ENTRY_CONTEXT


def test_close_position_perdida_marca_won_false(tmp_path):
    balance_path = str(tmp_path / "b.json")
    history_path = str(tmp_path / "h.json")
    position = {
        "wallet": "Theo", "token_mint": "XYZ", "config_id": "champion", "entry_price": 1.0,
        "amount_usd": 50.0, "opened_at": 1000.0, "root_score": 60, "root_prob": 0.6,
        "entry_context": None, "balance_path": balance_path, "history_path": history_path,
    }
    exit_info = {"reason": "stop_loss", "pnl_pct": -15.0}

    rsim._close_position(position, exit_info, current_price=0.85)

    history = [json.loads(l) for l in open(history_path).read().strip().splitlines()]
    assert history[0]["won"] is False
    assert history[0]["pnl_usd"] == pytest.approx(-7.5)


def test_close_position_dos_candidatos_no_comparten_balance(tmp_path):
    """Distintos config_id → distintos balance_path → no se mezclan."""
    b1, h1 = str(tmp_path / "b1.json"), str(tmp_path / "h1.json")
    b2, h2 = str(tmp_path / "b2.json"), str(tmp_path / "h2.json")
    pos1 = {"wallet": "A", "token_mint": "T1", "config_id": "gen0-0", "entry_price": 1.0,
            "amount_usd": 50.0, "opened_at": 0, "root_score": 1, "root_prob": 0.5,
            "entry_context": None, "balance_path": b1, "history_path": h1}
    pos2 = {"wallet": "B", "token_mint": "T1", "config_id": "gen0-1", "entry_price": 1.0,
            "amount_usd": 50.0, "opened_at": 0, "root_score": 1, "root_prob": 0.5,
            "entry_context": None, "balance_path": b2, "history_path": h2}

    rsim._close_position(pos1, {"reason": "max_hold", "pnl_pct": 10.0}, 1.1)
    rsim._close_position(pos2, {"reason": "max_hold", "pnl_pct": -10.0}, 0.9)

    bal1 = json.loads(open(b1).read())
    bal2 = json.loads(open(b2).read())
    assert bal1["balance"] == pytest.approx(rsim.INITIAL_BALANCE + 5.0)
    assert bal2["balance"] == pytest.approx(rsim.INITIAL_BALANCE - 5.0)


# ── open_position: gating + multi-config (sin lanzar hilos reales) ─────────

def test_open_position_no_abre_sin_entry_price(monkeypatch):
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: pytest.fail("no debería crear Thread"))
    rsim.open_position("Cented", "MINT1", {}, root_score=80, root_prob=0.8)
    assert not rsim._positions


def test_open_position_no_abre_sin_pair_address(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: None)
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: pytest.fail("no debería crear Thread"))
    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8)
    assert not rsim._positions


def test_open_position_no_abre_dos_veces_la_misma_config_y_token(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    calls = []
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: calls.append(1) or _FakeThread())

    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8, config_id="champion")
    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8, config_id="champion")

    assert len(calls) == 1


def test_open_position_mismo_token_distinta_config_no_choca(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: _FakeThread())

    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8, config_id="champion")
    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8, config_id="gen0-0")

    assert "champion:MINT1" in rsim._positions
    assert "gen0-0:MINT1" in rsim._positions


def test_open_position_no_abre_si_balance_agotado(tmp_path, monkeypatch):
    open(str(tmp_path / "root_sim_balance.json"), "w").write(
        json.dumps({"balance": 0, "initial": rsim.INITIAL_BALANCE})
    )
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: pytest.fail("no debería crear Thread"))
    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8)
    assert not rsim._positions


def test_open_position_abre_y_registra_posicion_con_defaults(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: _FakeThread())

    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8)

    pos = rsim._positions["champion:MINT1"]
    assert pos["wallet"] == "Cented"
    assert pos["entry_price"] == ENTRY_CONTEXT["price_usd"]
    assert pos["pair_address"] == "PAIR1"
    assert pos["amount_usd"] == rsim.TRADE_USD
    assert pos["stop_loss_pct"] == rsim.STOP_LOSS_PCT
    assert pos["max_hold_min"] == rsim.MAX_HOLD_MIN
    assert pos["balance_path"] == rsim.BALANCE_PATH


def test_open_position_respeta_overrides_de_candidato(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: _FakeThread())

    rsim.open_position(
        "Cented", "MINT1", ENTRY_CONTEXT, root_score=0, root_prob=0.0,
        config_id="gen0-0", stop_loss_pct=20.0, max_hold_min=10.0, trade_usd=75.0,
        balance_path="/tmp/custom_balance.json", history_path="/tmp/custom_history.json",
    )

    pos = rsim._positions["gen0-0:MINT1"]
    assert pos["stop_loss_pct"] == 20.0
    assert pos["max_hold_min"] == 10.0
    assert pos["amount_usd"] == 75.0
    assert pos["balance_path"] == "/tmp/custom_balance.json"


class _FakeThread:
    def start(self):
        pass

import pytest

from copytrade.root_config import neutral_config
from copytrade.root_backtest import simulate_exit, backtest_config


def test_simulate_exit_stop_loss():
    snapshots = [[0.0, 1.0], [15.0, 0.90], [30.0, 0.80], [45.0, 0.70]]
    pnl_pct, reason = simulate_exit(1.0, snapshots, stop_loss_pct=15.0, max_hold_min=30)
    assert reason == "stop_loss"
    assert pnl_pct == pytest.approx(-20.0)  # primer punto que cruza -15%: precio 0.80


def test_simulate_exit_max_hold():
    snapshots = [[0.0, 1.0], [900.0, 1.05], [1800.0, 1.08]]  # 15min, 30min
    pnl_pct, reason = simulate_exit(1.0, snapshots, stop_loss_pct=15.0, max_hold_min=30)
    assert reason == "max_hold"
    assert pnl_pct == pytest.approx(8.0)


def test_simulate_exit_sin_datos():
    pnl_pct, reason = simulate_exit(1.0, [], stop_loss_pct=15.0, max_hold_min=30)
    assert reason == "sin_datos"
    assert pnl_pct == 0.0


def test_simulate_exit_end_of_data_sin_cruzar_ningun_umbral():
    snapshots = [[0.0, 1.0], [60.0, 1.02]]
    pnl_pct, reason = simulate_exit(1.0, snapshots, stop_loss_pct=50.0, max_hold_min=999)
    assert reason == "end_of_data"
    assert pnl_pct == pytest.approx(2.0)


def test_backtest_config_solo_cuenta_trades_que_hubiera_copiado():
    cfg = neutral_config()
    cfg.bias = -1.0  # nunca copia (score siempre negativo con pesos en 0)
    dataset = [
        {"wallet": "X", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 50.0, "won": True, "trajectory": None},
    ]
    result = backtest_config(cfg, dataset)
    assert result["n_taken"] == 0
    assert result["pnl_usd_total"] == 0.0


def test_backtest_config_usa_pnl_registrado_sin_trayectoria():
    cfg = neutral_config()  # bias=0 → copia todo
    cfg.trade_usd = 50.0
    dataset = [
        {"wallet": "X", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 20.0, "won": True, "trajectory": None},
        {"wallet": "Y", "entry_context": {}, "entry_price": 1.0, "pnl_pct": -10.0, "won": False, "trajectory": None},
    ]
    result = backtest_config(cfg, dataset)
    assert result["n_taken"] == 2
    assert result["pnl_usd_total"] == pytest.approx(50 * 0.20 + 50 * -0.10)
    assert result["win_rate"] == pytest.approx(50.0)


def test_backtest_config_usa_trayectoria_cuando_existe():
    cfg = neutral_config()
    cfg.stop_loss_pct = 15.0
    cfg.max_hold_min = 30.0
    cfg.trade_usd = 50.0
    dataset = [
        {
            "wallet": "X", "entry_context": {}, "entry_price": 1.0,
            "pnl_pct": 999.0,  # si esto se usara en vez de la trayectoria, el test fallaría
            "won": True,
            "trajectory": [[0.0, 1.0], [15.0, 0.80]],  # -20% → dispara stop_loss antes
        },
    ]
    result = backtest_config(cfg, dataset)
    assert result["pnl_usd_total"] == pytest.approx(50 * -0.20)


def test_backtest_config_excluding_best_resta_el_mejor_trade():
    cfg = neutral_config()
    cfg.trade_usd = 50.0
    dataset = [
        {"wallet": "X", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 200.0, "won": True, "trajectory": None},
        {"wallet": "Y", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 10.0, "won": True, "trajectory": None},
    ]
    result = backtest_config(cfg, dataset)
    best = 50 * 2.0
    assert result["pnl_usd_excluding_best"] == pytest.approx(result["pnl_usd_total"] - best)

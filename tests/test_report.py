import time
from datetime import datetime

from copytrade.report import compute_summary, format_report_message


def _trade(hours_ago, won, pnl_usd, pnl_pct, symbol="ABC", wallet="Theo"):
    now = time.time()
    return {
        "timestamp": now - hours_ago * 3600,
        "won": won,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "symbol": symbol,
        "wallet_label": wallet,
    }


def test_compute_summary_no_trades():
    now = time.time()
    summary = compute_summary([], {"balance": 100.0, "initial": 20.0}, {}, now)
    assert summary["trades_24h"] == 0
    assert summary["win_rate_24h"] == 0.0
    assert summary["pnl_24h_usd"] == 0.0
    assert summary["best_trade"] is None
    assert summary["worst_trade"] is None
    assert summary["open_positions"] == 0
    assert summary["roi_pct"] == 400.0


def test_compute_summary_mixed_trades_within_window():
    now = time.time()
    history = [
        _trade(1, True, 50.0, 20.0, symbol="WIN1"),
        _trade(2, False, -10.0, -5.0, symbol="LOSS1"),
        _trade(25, True, 999.0, 999.0, symbol="TOO_OLD"),
    ]
    summary = compute_summary(history, {"balance": 140.0, "initial": 20.0}, {"a": {}}, now)
    assert summary["trades_24h"] == 2
    assert summary["wins_24h"] == 1
    assert summary["losses_24h"] == 1
    assert summary["win_rate_24h"] == 50.0
    assert summary["pnl_24h_usd"] == 40.0
    assert summary["best_trade"]["symbol"] == "WIN1"
    assert summary["worst_trade"]["symbol"] == "LOSS1"
    assert summary["open_positions"] == 1


def test_compute_summary_all_losses_best_is_least_bad():
    now = time.time()
    history = [
        _trade(1, False, -10.0, -5.0, symbol="L1"),
        _trade(2, False, -20.0, -8.0, symbol="L2"),
    ]
    summary = compute_summary(history, {"balance": 70.0, "initial": 100.0}, {}, now)
    assert summary["win_rate_24h"] == 0.0
    assert summary["best_trade"]["symbol"] == "L1"
    assert summary["worst_trade"]["symbol"] == "L2"


def test_format_report_message_with_activity():
    summary = {
        "trades_24h": 2, "wins_24h": 1, "losses_24h": 1, "win_rate_24h": 50.0,
        "pnl_24h_usd": 40.0,
        "best_trade": {"symbol": "WIN1", "wallet_label": "Theo", "pnl_pct": 20.0},
        "worst_trade": {"symbol": "LOSS1", "wallet_label": "Cented", "pnl_pct": -5.0},
        "open_positions": 3, "balance": 140.0, "initial": 20.0, "roi_pct": 600.0,
    }
    msg = format_report_message(summary, bot_alive=True, now_dt=datetime(2026, 7, 23, 21, 0))
    assert "🟢 corriendo" in msg
    assert "Balance: $140.00 (inicial $20.00 → ROI +600.0%)" in msg
    assert "2 trades" in msg
    assert "WR 50%" in msg
    assert "Mejor: WIN1 Theo +20.0%" in msg
    assert "Peor: LOSS1 Cented -5.0%" in msg
    assert "Posiciones abiertas: 3" in msg


def test_format_report_message_no_activity_bot_down():
    summary = {
        "trades_24h": 0, "wins_24h": 0, "losses_24h": 0, "win_rate_24h": 0.0,
        "pnl_24h_usd": 0.0, "best_trade": None, "worst_trade": None,
        "open_positions": 0, "balance": 20.0, "initial": 20.0, "roi_pct": 0.0,
    }
    msg = format_report_message(summary, bot_alive=False, now_dt=datetime(2026, 7, 23, 21, 0))
    assert "🔴 proceso caído" in msg
    assert "sin actividad" in msg
    assert "Mejor" not in msg
    assert "Peor" not in msg

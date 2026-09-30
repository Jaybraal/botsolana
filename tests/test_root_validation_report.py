from copytrade.root_validation_report import summarize


def _trade(pnl):
    return {"pnl_usd": pnl, "entry_liquidity_usd": 5000, "simulation_model": "root-v2-net-execution"}


def test_summary_uses_only_executable_net_records():
    report = summarize("gen-1", [_trade(10), _trade(-5), {"pnl_usd": 1000}])
    assert report == {
        "config_id": "gen-1", "eligible_trades": 2, "net_pnl_usd": 5.0,
        "profit_factor": 2.0, "max_drawdown_usd": 5.0, "eligible": True,
    }


def test_summary_marks_empty_eligible_sample():
    report = summarize("gen-1", [{"pnl_usd": 1000}])
    assert report["eligible"] is False
    assert report["eligible_trades"] == 0

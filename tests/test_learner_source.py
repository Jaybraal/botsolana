import json
import os
import pytest


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """Redirige HISTORY_FILE y RULES_FILE a tmp_path."""
    import copytrade.learner as L
    monkeypatch.setattr(L, "HISTORY_FILE", str(tmp_path / "sim_history.json"))
    monkeypatch.setattr(L, "RULES_FILE",   str(tmp_path / "learner_rules.json"))
    monkeypatch.setattr(L, "RULES_CW_FILE",   str(tmp_path / "learner_rules_copywallet.json"))
    monkeypatch.setattr(L, "RULES_AUTO_FILE",  str(tmp_path / "learner_rules_auto.json"))
    return tmp_path


def _make_trade(wallet_label: str, won: bool, mcap: float = 30000.0) -> dict:
    return {
        "wallet_label": wallet_label,
        "won": won,
        "entry_context": {
            "mcap_usd": mcap,
            "liquidity_usd": 5000.0,
            "volume_24h_usd": 50000.0,
            "vol_liq_ratio": 4.5,
            "buy_pressure": 0.60,
            "age_days": 2.5,
            "change_1h_pct": 95.0,
        },
    }


def test_update_generates_copywallet_file(tmp_data):
    import copytrade.learner as L
    trades = [_make_trade("Decu", True) for _ in range(3)] + \
             [_make_trade("Decu", False) for _ in range(3)]
    with open(L.HISTORY_FILE, "w") as f:
        json.dump(trades, f)

    L.update()

    assert os.path.exists(L.RULES_CW_FILE), "learner_rules_copywallet.json debe crearse"
    rules = json.loads(open(L.RULES_CW_FILE).read())
    assert rules["total_trades"] == 6
    assert "scoring_rules" in rules


def test_update_generates_auto_file(tmp_data):
    import copytrade.learner as L
    trades = [_make_trade("AUTO 🤖", True) for _ in range(3)] + \
             [_make_trade("AUTO 🤖", False) for _ in range(3)]
    with open(L.HISTORY_FILE, "w") as f:
        json.dump(trades, f)

    L.update()

    assert os.path.exists(L.RULES_AUTO_FILE), "learner_rules_auto.json debe crearse"
    rules = json.loads(open(L.RULES_AUTO_FILE).read())
    assert rules["total_trades"] == 6


def test_update_separates_sources(tmp_data):
    import copytrade.learner as L
    trades = (
        [_make_trade("Decu", True) for _ in range(4)] +
        [_make_trade("AUTO 🤖", True) for _ in range(2)] +
        [_make_trade("AUTO 🤖", False) for _ in range(4)]
    )
    with open(L.HISTORY_FILE, "w") as f:
        json.dump(trades, f)

    L.update()

    cw   = json.loads(open(L.RULES_CW_FILE).read())
    auto = json.loads(open(L.RULES_AUTO_FILE).read())
    assert cw["total_trades"] == 4
    assert auto["total_trades"] == 6


def test_load_rules_by_source(tmp_data):
    import copytrade.learner as L
    trades = [_make_trade("Decu", True) for _ in range(5)] + \
             [_make_trade("Decu", False) for _ in range(3)]
    with open(L.HISTORY_FILE, "w") as f:
        json.dump(trades, f)
    L.update()

    rules_cw = L.load_rules(source="CW")
    assert rules_cw.get("total_trades") == 8

    rules_auto = L.load_rules(source="AUTO")
    # AUTO file existe pero vacío (0 trades AUTO) — retorna {}
    assert rules_auto == {} or rules_auto.get("total_trades", 0) == 0


def test_update_skips_auto_file_when_no_auto_trades(tmp_data):
    import copytrade.learner as L
    trades = [_make_trade("Theo", True) for _ in range(5)] + \
             [_make_trade("Theo", False) for _ in range(3)]
    with open(L.HISTORY_FILE, "w") as f:
        json.dump(trades, f)

    L.update()

    # Si no hay trades AUTO, el archivo no se crea (o está vacío)
    if os.path.exists(L.RULES_AUTO_FILE):
        rules = json.loads(open(L.RULES_AUTO_FILE).read())
        assert rules.get("total_trades", 0) == 0

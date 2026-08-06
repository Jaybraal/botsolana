import pytest

from copytrade.root_wallet_miner import wallet_features, initial_wallet_trust

DATASET = [
    {"wallet": "Theo", "entry_context": {"change_1h_pct": 100.0}, "pnl_pct": 20.0, "won": True},
    {"wallet": "Theo", "entry_context": {"change_1h_pct": 50.0}, "pnl_pct": -10.0, "won": False},
    {"wallet": "Decu", "entry_context": {}, "pnl_pct": -5.0, "won": False},
]


def test_wallet_features_agrupa_y_calcula_winrate():
    feats = wallet_features(DATASET)
    assert feats["Theo"]["n_trades"] == 2
    assert feats["Theo"]["win_rate"] == pytest.approx(50.0)
    assert feats["Theo"]["avg_pnl_pct"] == pytest.approx(5.0)
    assert feats["Theo"]["avg_change_1h_pct_al_entrar"] == pytest.approx(75.0)
    assert feats["Decu"]["win_rate"] == pytest.approx(0.0)
    assert feats["Decu"]["avg_change_1h_pct_al_entrar"] is None


def test_wallet_features_dataset_vacio():
    assert wallet_features([]) == {}


def test_initial_wallet_trust_centra_en_50_por_ciento():
    trust = initial_wallet_trust(DATASET, scale=0.02)
    # Theo: winrate 50% → (50-50)*0.02 = 0.0
    assert trust["Theo"] == pytest.approx(0.0)
    # Decu: winrate 0% → (0-50)*0.02 = -1.0
    assert trust["Decu"] == pytest.approx(-1.0)

import pytest

import copytrade.root_wallet_miner as rwm
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


def test_blend_with_gmgn_mezcla_prior_propio_y_datos_reales(monkeypatch):
    monkeypatch.setattr(
        rwm, "get_wallet_stats",
        lambda address, chain="sol": {"win_rate": 80.0} if address == "WALLET1" else None,
    )
    trust = {"WALLET1": 0.0}
    blended = rwm.blend_with_gmgn(trust, ["WALLET1"], gmgn_weight=0.5)
    # gmgn_trust = (80-50)*0.02 = 0.6 ; own=0.0 ; blend = 0*0.5 + 0.6*0.5 = 0.3
    assert blended["WALLET1"] == pytest.approx(0.3)


def test_blend_with_gmgn_sin_datos_gmgn_deja_valor_original(monkeypatch):
    monkeypatch.setattr(rwm, "get_wallet_stats", lambda address, chain="sol": None)
    trust = {"WALLET1": 0.42}
    blended = rwm.blend_with_gmgn(trust, ["WALLET1"])
    assert blended["WALLET1"] == pytest.approx(0.42)


def test_blend_with_gmgn_wallet_nueva_sin_prior_previo(monkeypatch):
    monkeypatch.setattr(rwm, "get_wallet_stats", lambda address, chain="sol": {"win_rate": 90.0})
    blended = rwm.blend_with_gmgn({}, ["NUEVA"], gmgn_weight=1.0)
    # sin prior propio (default 0.0), peso 1.0 → puro gmgn_trust = (90-50)*0.02 = 0.8
    assert blended["NUEVA"] == pytest.approx(0.8)


def test_blend_with_gmgn_no_muta_el_dict_original(monkeypatch):
    monkeypatch.setattr(rwm, "get_wallet_stats", lambda address, chain="sol": {"win_rate": 80.0})
    original = {"WALLET1": 0.0}
    rwm.blend_with_gmgn(original, ["WALLET1"])
    assert original["WALLET1"] == 0.0

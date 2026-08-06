import pytest

from copytrade.root_config import (
    FEATURES, Config, neutral_config, score_entry_context, decide,
    config_to_dict, config_from_dict,
)


def test_neutral_config_pesos_en_cero():
    cfg = neutral_config("test-seed")
    assert cfg.config_id == "test-seed"
    assert all(cfg.weights[f] == 0.0 for f in FEATURES)
    assert cfg.bias == 0.0
    assert cfg.wallet_trust == {}


def test_score_con_pesos_en_cero_es_solo_bias_mas_trust():
    cfg = neutral_config()
    cfg.bias = 5.0
    cfg.wallet_trust = {"Cupsey": 2.0}
    score = score_entry_context(cfg, "Cupsey", {"mcap_usd": 100000, "buy_pressure": 0.9})
    assert score == pytest.approx(7.0)


def test_score_usa_log1p_para_mcap():
    cfg = neutral_config()
    cfg.weights["mcap_usd"] = 1.0
    import math
    score = score_entry_context(cfg, "X", {"mcap_usd": 999})
    assert score == pytest.approx(math.log1p(999))


def test_score_ignora_features_faltantes_sin_reventar():
    cfg = neutral_config()
    cfg.weights["change_1h_pct"] = 1.0
    score = score_entry_context(cfg, "X", {})
    assert score == 0.0


def test_decide_copiar_con_score_positivo():
    cfg = neutral_config()
    cfg.bias = 1.0
    assert decide(cfg, "X", {}) == "COPIAR"


def test_decide_skip_con_score_negativo():
    cfg = neutral_config()
    cfg.bias = -1.0
    assert decide(cfg, "X", {}) == "SKIP"


def test_decide_score_cero_copia_por_defecto():
    cfg = neutral_config()
    assert decide(cfg, "X", {}) == "COPIAR"


def test_config_roundtrip_dict():
    cfg = neutral_config("roundtrip")
    cfg.weights["buy_pressure"] = 0.42
    cfg.wallet_trust = {"Theo": 0.1}
    cfg.stop_loss_pct = 12.0
    restored = config_from_dict(config_to_dict(cfg))
    assert restored == cfg

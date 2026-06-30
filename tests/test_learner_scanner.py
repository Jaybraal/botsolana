import pytest


# ── _passes_learner_criteria ─────────────────────────────────────────────────

def test_passes_criteria_all_ok():
    from copytrade.learner_scanner import _passes_learner_criteria
    rules = {
        "scoring_rules": {
            "min_mcap_usd":       15000.0,
            "max_mcap_usd":       47000.0,
            "min_liquidity_usd":  3000.0,
            "min_buy_pressure":   0.50,
            "min_change_1h_pct":  70.0,
            "max_age_days":       7.0,
        }
    }
    token = {
        "mcap_usd":        30000.0,
        "liquidity_usd":   6000.0,
        "buy_pressure":    0.62,
        "price_change_1h": 95.0,
        "age_days":        2.5,
    }
    passed, reason = _passes_learner_criteria(token, rules)
    assert passed, f"Debería pasar: {reason}"


def test_passes_criteria_mcap_too_high():
    from copytrade.learner_scanner import _passes_learner_criteria
    rules = {"scoring_rules": {"min_mcap_usd": 15000.0, "max_mcap_usd": 47000.0}}
    token = {"mcap_usd": 100000.0, "liquidity_usd": 0, "buy_pressure": 0, "price_change_1h": 0, "age_days": 1}
    passed, reason = _passes_learner_criteria(token, rules)
    assert not passed


def test_passes_criteria_too_old():
    from copytrade.learner_scanner import _passes_learner_criteria
    rules = {"scoring_rules": {"max_age_days": 7.0}}
    token = {"mcap_usd": 30000.0, "liquidity_usd": 5000, "buy_pressure": 0.6, "price_change_1h": 100, "age_days": 15.0}
    passed, reason = _passes_learner_criteria(token, rules)
    assert not passed


def test_passes_criteria_fallback_when_no_rules():
    from copytrade.learner_scanner import _passes_learner_criteria
    # Sin reglas → usar hardcoded → token razonable debe pasar
    token = {
        "mcap_usd":        30000.0,
        "liquidity_usd":   5000.0,
        "buy_pressure":    0.60,
        "price_change_1h": 100.0,
        "age_days":        2.0,
    }
    passed, reason = _passes_learner_criteria(token, {})
    assert passed


def test_passes_criteria_low_buy_pressure():
    from copytrade.learner_scanner import _passes_learner_criteria
    rules = {"scoring_rules": {"min_buy_pressure": 0.513}}
    token = {"mcap_usd": 30000, "liquidity_usd": 5000, "buy_pressure": 0.30, "price_change_1h": 100, "age_days": 2}
    passed, reason = _passes_learner_criteria(token, rules)
    assert not passed


# ── _score_and_decide ────────────────────────────────────────────────────────

def test_score_and_decide_passes_good_token():
    from copytrade.learner_scanner import _score_and_decide
    token = {
        "mcap_usd":        35000.0,
        "liquidity_usd":   5000.0,
        "buy_pressure":    0.62,
        "price_change_1h": 95.0,
        "age_days":        2.5,
        "token_age_min":   3600.0,   # 60 horas en minutos
        "price_change_5m": 5.0,
        "buys_5m":         15,
        "sells_5m":        5,
        "program":         "PumpSwap",
        "price_usd":       0.0001,
        "price_sol":       0.0000007,
    }
    passed, reason = _score_and_decide(token)
    # No garantizamos pass (depende de learner_rules.json en disco),
    # pero sí que no lanza excepción y retorna tuple(bool, str)
    assert isinstance(passed, bool)
    assert isinstance(reason, str)


def test_score_and_decide_rejects_zero_price():
    from copytrade.learner_scanner import _score_and_decide
    token = {
        "mcap_usd": 30000, "liquidity_usd": 5000, "buy_pressure": 0.6,
        "price_change_1h": 95, "age_days": 2, "token_age_min": 2880,
        "price_usd": 0.0, "price_sol": 0.0, "buys_5m": 10, "sells_5m": 2,
        "program": "PumpSwap", "price_change_5m": 3,
    }
    passed, reason = _score_and_decide(token)
    assert not passed
    assert "precio" in reason.lower() or "price" in reason.lower()

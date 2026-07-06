import copytrade.learner_scanner as ls


def _token(liq=20000.0, ch1=80.0):
    return {
        "price_usd": 1.0,
        "symbol": "TST",
        "liquidity_usd": liq,
        "price_change_1h": ch1,
    }


def test_rechaza_liquidez_baja():
    ok, reason = ls._score_and_decide(_token(liq=5000.0))
    assert ok is False
    assert "liquidez" in reason.lower()


def test_rechaza_momentum_bajo():
    ok, reason = ls._score_and_decide(_token(ch1=10.0))
    assert ok is False
    assert "change_1h" in reason.lower()


def test_filtro_duro_no_depende_de_otros_criterios():
    # liquidez y momentum buenos en un token que fallaría el resto:
    # el rechazo (si ocurre) NO debe ser por los filtros duros
    ok, reason = ls._score_and_decide(_token(liq=45000.0, ch1=120.0))
    assert "filtro duro" not in reason.lower()

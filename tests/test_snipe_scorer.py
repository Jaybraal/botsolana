import json, os, pytest
from pathlib import Path

MOCK_PATTERNS = {
    "generated_at": "2026-06-26T00:00:00+00:00",
    "total_trades": 2487,
    "age_buckets": [
        {"label": "<1min",    "min": 0,  "max": 1,    "wr": 79.1, "n": 829, "score_pts": 15},
        {"label": "1-3min",   "min": 1,  "max": 3,    "wr": 90.6, "n": 287, "score_pts": 40},
        {"label": "3-10min",  "min": 3,  "max": 10,   "wr": 87.3, "n": 157, "score_pts": 35},
        {"label": "10-30min", "min": 10, "max": 30,   "wr": 81.6, "n": 158, "score_pts": 15},
        {"label": "30+min",   "min": 30, "max": 9999, "wr": 78.9, "n": 147, "score_pts": 5 },
    ],
    "mcap_buckets": [
        {"label": "<10k",    "min": 0,      "max": 10000,     "wr": 81.6, "score_pts": 15},
        {"label": "10-30k",  "min": 10000,  "max": 30000,     "wr": 81.9, "score_pts": 20},
        {"label": "30-70k",  "min": 30000,  "max": 70000,     "wr": 91.3, "score_pts": 30},
        {"label": "70-150k", "min": 70000,  "max": 150000,    "wr": 87.5, "score_pts": 20},
        {"label": "150k+",   "min": 150000, "max": 999999999, "wr": 74.5, "score_pts": 5 },
    ],
    "buys_buckets": [
        {"label": "<10",     "min": 0,   "max": 10,    "wr": 75.0, "score_pts": 5 },
        {"label": "10-50",   "min": 10,  "max": 50,    "wr": 78.0, "score_pts": 10},
        {"label": "50-150",  "min": 50,  "max": 150,   "wr": 90.5, "score_pts": 20},
        {"label": "150-300", "min": 150, "max": 300,   "wr": 85.0, "score_pts": 15},
        {"label": "300+",    "min": 300, "max": 999999,"wr": 87.5, "score_pts": 15},
    ],
    "elite_wallet_boost": 15,
    "buy_threshold": 55,
}


@pytest.fixture(autouse=True)
def patch_patterns(tmp_path):
    p = tmp_path / "snipe_patterns.json"
    p.write_text(json.dumps(MOCK_PATTERNS))
    os.environ["PATTERNS_PATH"] = str(p)
    import importlib
    import copytrade.snipe_scorer as m
    m._patterns = None
    yield
    m._patterns = None
    os.environ.pop("PATTERNS_PATH", None)


def test_gold_zone_passes():
    from copytrade.snipe_scorer import score_token
    # edad 1-3min (+40) + mcap 30-70k (+30) + buys 50-150 (+20) = 90 → PASS
    score, passed, reason = score_token({
        "token_age_min": 2.0,
        "mcap_usd": 50000,
        "buys_5m": 80,
    })
    assert passed
    assert score == 90
    assert "1-3min" in reason
    assert "30-70k" in reason


def test_old_token_fails():
    from copytrade.snipe_scorer import score_token
    # edad 30+min (+5) + mcap <10k (+15) + buys <10 (+5) = 25 → FAIL
    score, passed, _ = score_token({
        "token_age_min": 45.0,
        "mcap_usd": 5000,
        "buys_5m": 3,
    })
    assert not passed
    assert score == 25


def test_elite_boost_adds_15():
    from copytrade.snipe_scorer import score_token
    base_score, _, _ = score_token({"token_age_min": 2.0, "mcap_usd": 5000, "buys_5m": 3})
    boosted_score, _, reason = score_token(
        {"token_age_min": 2.0, "mcap_usd": 5000, "buys_5m": 3},
        elite_signal=True,
    )
    assert boosted_score == base_score + 15
    assert "élite" in reason


def test_empty_token_info_no_crash():
    from copytrade.snipe_scorer import score_token
    score, passed, reason = score_token({})
    assert isinstance(score, int)
    assert isinstance(passed, bool)
    assert reason == "sin señales"


def test_score_clamped_0_100():
    from copytrade.snipe_scorer import score_token
    score, _, _ = score_token({
        "token_age_min": 2.0,
        "mcap_usd": 50000,
        "buys_5m": 200,
        # todos los buckets máximos + boost = potencialmente >100
    }, elite_signal=True)
    assert 0 <= score <= 100


def test_missing_patterns_raises():
    import copytrade.snipe_scorer as m
    m._patterns = None
    os.environ["PATTERNS_PATH"] = "/nonexistent/path.json"
    with pytest.raises(FileNotFoundError, match="snipe_patterns.json"):
        m.score_token({"token_age_min": 2.0})

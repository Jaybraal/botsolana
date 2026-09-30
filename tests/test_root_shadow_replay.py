from copytrade.root_config import neutral_config
from copytrade.root_shadow_replay import replay


def test_replay_counts_decisions_without_opening_positions():
    result = replay([
        {"wallet": "A", "entry_context": {"price_usd": 1e-5, "liquidity_usd": 5000}},
        {"wallet": "B", "entry_context": {"price_usd": 1e-5, "liquidity_usd": 0}},
    ], neutral_config())
    assert result["signals"] == 2
    assert result["copied"] >= result["executable_copies"]
    assert "no es evidencia de rentabilidad" in result["note"]

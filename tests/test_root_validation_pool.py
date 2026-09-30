import json

import pytest

import copytrade.root_validation_pool as pool
from copytrade.root_config import neutral_config, config_to_dict


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(pool, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pool, "CHAMPION_PATH", str(tmp_path / "root_champion.json"))
    pool._active_candidates.clear()
    yield
    pool._active_candidates.clear()


def _write_history(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _trade(pnl_usd):
    return {
        "pnl_usd": pnl_usd,
        "entry_liquidity_usd": 10_000,
        "simulation_model": "root-v2-net-execution",
    }


def test_register_and_active_candidates():
    cfg = neutral_config("gen0-0")
    pool.register_candidates([cfg])
    assert [c.config_id for c in pool.active_candidates()] == ["gen0-0"]


def test_promote_if_ready_falla_con_muestra_insuficiente(tmp_path):
    candidate = neutral_config("gen0-0")
    _write_history(pool._history_path("gen0-0"), [_trade(10.0)] * 5)  # menos de 20
    champion_history = [_trade(5.0)] * 25
    promoted = pool.promote_if_ready(candidate, champion_history)
    assert promoted is False
    assert not tmp_path.joinpath("root_champion.json").exists()


def test_promote_if_ready_promueve_con_ventaja_robusta(tmp_path):
    candidate = neutral_config("gen0-0")
    cand_history = [_trade(10.0)] * 25  # excl. mejor: 24*10=240
    _write_history(pool._history_path("gen0-0"), cand_history)
    champion_history = [_trade(1.0)] * 25  # excl. mejor: 24*1=24

    promoted = pool.promote_if_ready(candidate, champion_history)

    assert promoted is True
    saved = json.loads(tmp_path.joinpath("root_champion.json").read_text())
    assert saved["config_id"] == "gen0-0"


def test_promote_if_ready_no_promueve_por_un_solo_outlier(tmp_path):
    """El candidato gana en total solo por un trade gigante — excluyendo el
    mejor de cada lado, pierde. No debe promover."""
    candidate = neutral_config("gen0-0")
    cand_history = [_trade(500.0)] + [_trade(-5.0)] * 24  # excl. mejor: -120
    _write_history(pool._history_path("gen0-0"), cand_history)
    champion_history = [_trade(2.0)] * 25  # excl. mejor: 24*2=48

    promoted = pool.promote_if_ready(candidate, champion_history)

    assert promoted is False


def test_promote_if_ready_excluye_historial_sin_liquidez_o_costes(tmp_path):
    candidate = neutral_config("gen0-0")
    _write_history(pool._history_path("gen0-0"), [{"pnl_usd": 100.0}] * 25)
    promoted = pool.promote_if_ready(candidate, [_trade(1.0)] * 25)
    assert promoted is False


def test_promote_if_ready_exige_factor_de_beneficio(tmp_path):
    candidate = neutral_config("gen0-0")
    # P&L positivo y superior al campeón, pero pérdidas agregadas hacen PF < 1.20.
    _write_history(pool._history_path("gen0-0"), [_trade(11.0)] * 13 + [_trade(-10.0)] * 12)
    promoted = pool.promote_if_ready(candidate, [_trade(0.1)] * 25)
    assert promoted is False


def test_promote_candidates_primer_arranque_guarda_el_primero_sin_campeon(tmp_path):
    candidates = [neutral_config("gen0-0"), neutral_config("gen0-1")]
    pool.promote_candidates(candidates, dataset=[])
    saved = json.loads(tmp_path.joinpath("root_champion.json").read_text())
    assert saved["config_id"] == "gen0-0"

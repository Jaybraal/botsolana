import random

import pytest

from copytrade.root_config import FEATURES, neutral_config
from copytrade.root_evolution import mutate, initial_population, run_generation


def test_mutate_cambia_config_id():
    cfg = neutral_config("origen")
    child = mutate(cfg, config_id="hijo")
    assert child.config_id == "hijo"
    assert cfg.config_id == "origen"  # no muta el original


def test_mutate_no_modifica_el_padre():
    cfg = neutral_config("origen")
    mutate(cfg, config_id="hijo", std=0.5)
    assert all(cfg.weights[f] == 0.0 for f in FEATURES)  # el padre queda intacto


def test_mutate_produce_variacion(monkeypatch):
    random.seed(42)
    cfg = neutral_config("origen")
    child = mutate(cfg, config_id="hijo", std=1.0)
    assert any(child.weights[f] != 0.0 for f in FEATURES) or child.bias != 0.0


def test_initial_population_tiene_el_tamano_pedido():
    seed = neutral_config("seed")
    population = initial_population(seed, size=6)
    assert len(population) == 6
    assert population[0] is seed  # la semilla siempre está, sin mutar


def test_run_generation_devuelve_mismo_tamano_que_entrada():
    seed = neutral_config("seed")
    population = initial_population(seed, size=8)
    dataset = [
        {"wallet": "X", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 5.0, "won": True, "trajectory": None},
    ]
    next_gen = run_generation(population, dataset, generation=1, top_k=3)
    assert len(next_gen) == 8


def test_run_generation_conserva_a_los_sobrevivientes():
    """Una config con bias muy negativo nunca copia nada → pnl_excluding_best
    0 (no toma ningún trade). Otra con bias positivo copia los dos trades
    ganadores del dataset → excluyendo el mejor, todavía le queda el otro
    con pnl > 0. La segunda debe sobrevivir a la siguiente generación."""
    loser = neutral_config("loser")
    loser.bias = -100.0
    winner = neutral_config("winner")
    winner.bias = 100.0
    population = [loser, winner]
    dataset = [
        {"wallet": "X", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 20.0, "won": True, "trajectory": None},
        {"wallet": "Y", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 10.0, "won": True, "trajectory": None},
    ]
    next_gen = run_generation(population, dataset, generation=1, top_k=1)
    assert next_gen[0].config_id == "winner"

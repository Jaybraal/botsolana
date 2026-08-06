"""
Ciclo evolutivo: mantiene una población de Config, la evalúa contra el
dataset acumulado (semilla + trades propios + trayectorias), se queda con
las mejores y muta para la siguiente generación. Corre en un thread daemon
aparte — si un ciclo falla, la config campeón sigue operando sin cambios.
"""
import copy
import json
import os
import random
import threading
import time

from copytrade.root_backtest import backtest_config
from copytrade.root_config import Config, FEATURES, config_from_dict, config_to_dict, neutral_config
from copytrade.root_seed_loader import load_seed_dataset
from copytrade.root_trajectory import load_trajectories
from utils.logger import get_logger

log = get_logger("root_evolution")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
POPULATION_PATH = os.path.join(_DATA_DIR, "root_population.json")

ENABLED = os.getenv("ROOT_EVOLUTION_ENABLED", "true").lower() == "true"
INTERVAL_MIN = float(os.getenv("ROOT_EVOLUTION_INTERVAL_MIN", "30"))
POPULATION_SIZE = int(os.getenv("ROOT_EVOLUTION_POPULATION_SIZE", "12"))
TOP_K = int(os.getenv("ROOT_EVOLUTION_TOP_K", "4"))
MUTATION_STD = float(os.getenv("ROOT_EVOLUTION_MUTATION_STD", "0.15"))


def mutate(config: Config, config_id: str, std: float = MUTATION_STD) -> Config:
    """Variante de `config`: cada peso y el bias se mueven con ruido
    gaussiano; stop_loss/max_hold también mutan dentro de rangos
    razonables. Nada queda fijo — todo lo que decide es mutable. No toca
    al padre (deepcopy primero)."""
    new = copy.deepcopy(config)
    new.config_id = config_id
    for f in FEATURES:
        new.weights[f] = round(new.weights.get(f, 0.0) + random.gauss(0, std), 4)
    new.bias = round(new.bias + random.gauss(0, std), 4)
    new.stop_loss_pct = max(3.0, round(new.stop_loss_pct + random.gauss(0, 2.0), 2))
    new.max_hold_min = max(5.0, round(new.max_hold_min + random.gauss(0, 5.0), 2))
    return new


def initial_population(seed_config: Config, size: int = POPULATION_SIZE) -> list[Config]:
    """La primera generación: la config semilla (neutra) tal cual + el
    resto mutado desde ahí — para no arrancar de un solo punto ciego."""
    population = [seed_config]
    for i in range(size - 1):
        population.append(mutate(seed_config, config_id=f"gen0-{i}"))
    return population


def run_generation(population: list[Config], dataset: list[dict], generation: int, top_k: int = TOP_K) -> list[Config]:
    """Backtestea toda la población, se queda con las top_k por
    pnl_usd_excluding_best (para no seleccionar por un outlier), y genera
    la siguiente generación mutando esas. Devuelve una población del mismo
    tamaño que la de entrada."""
    scored = [(cfg, backtest_config(cfg, dataset)) for cfg in population]
    scored.sort(key=lambda cs: cs[1]["pnl_usd_excluding_best"], reverse=True)
    survivors = [cfg for cfg, _ in scored[:top_k]]

    next_population = list(survivors)
    i = 0
    while len(next_population) < len(population):
        parent = survivors[i % len(survivors)]
        next_population.append(mutate(parent, config_id=f"gen{generation}-{i}"))
        i += 1
    return next_population


def _load_population(seed_config: Config) -> list[Config]:
    if not os.path.exists(POPULATION_PATH):
        return initial_population(seed_config)
    try:
        raw = json.loads(open(POPULATION_PATH).read())
        return [config_from_dict(c) for c in raw]
    except (json.JSONDecodeError, OSError, KeyError):
        return initial_population(seed_config)


def _save_population(population: list[Config]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(POPULATION_PATH, "w") as f:
        json.dump([config_to_dict(c) for c in population], f, indent=2)


def build_dataset() -> list[dict]:
    """Junta el historial semilla con las trayectorias propias grabadas.
    Si el mismo mint se tradeó más de una vez, usa la primera trayectoria
    encontrada para ese mint — no hay id de posición compartido entre el
    historial semilla y root_trajectory para desambiguar mejor. Limitación
    conocida, documentada acá en vez de adivinar cuál corresponde."""
    seed = load_seed_dataset(
        os.path.join(_DATA_DIR, "sim_history.json"),
        os.path.join(_DATA_DIR, "root_sim_history.json"),
    )
    trajectories_by_mint: dict[str, list] = {}
    for traj in load_trajectories():
        trajectories_by_mint.setdefault(traj["token_mint"], []).append(traj["snapshots"])
    for trade in seed:
        candidates = trajectories_by_mint.get(trade.get("token_mint"))
        if candidates:
            trade["trajectory"] = candidates[0]
    return seed


def evolution_loop() -> None:
    """Corre para siempre en un thread daemon: cada INTERVAL_MIN minutos,
    evoluciona la población y manda a los mejores a validación en vivo.
    Cualquier excepción se loguea y se descarta — nunca tumba el bot ni deja
    de decidir con el campeón actual."""
    from copytrade.root_validation_pool import promote_candidates

    generation = 0
    seed_config = neutral_config()
    population = _load_population(seed_config)
    while True:
        try:
            dataset = build_dataset()
            population = run_generation(population, dataset, generation)
            _save_population(population)
            promote_candidates(population[:TOP_K], dataset)
            generation += 1
        except Exception as e:
            log.warning(f"[root_evolution] ciclo falló, sigue con el campeón actual — {e}")
        time.sleep(INTERVAL_MIN * 60)


def start() -> None:
    if not ENABLED:
        log.info("[root_evolution] deshabilitado (ROOT_EVOLUTION_ENABLED=false)")
        return
    threading.Thread(target=evolution_loop, daemon=True).start()
    log.info(f"[root_evolution] arrancado — cada {INTERVAL_MIN}min, población={POPULATION_SIZE}")

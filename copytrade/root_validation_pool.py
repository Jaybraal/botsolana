"""
Corre los top-K candidatos de cada generación en paper-trading real, cada
uno con su propio balance de papel, para confirmar contra la realidad
antes de que el evolutivo los promueva a campeón. Reutiliza la mecánica de
apertura/monitoreo de root_sim.py, parametrizada por config y con
persistencia separada por config_id.
"""
import json
import os
import threading

from copytrade import root_sim
from copytrade.root_config import Config, config_from_dict, config_to_dict
from utils.logger import get_logger

log = get_logger("root_validation_pool")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CHAMPION_PATH = os.path.join(_DATA_DIR, "root_champion.json")
PROMOTION_MIN_TRADES = int(os.getenv("ROOT_PROMOTION_MIN_TRADES", "20"))

_active_candidates: dict[str, Config] = {}
_lock = threading.Lock()


def _balance_path(config_id: str) -> str:
    return os.path.join(_DATA_DIR, f"root_validation_{config_id}_balance.json")


def _history_path(config_id: str) -> str:
    return os.path.join(_DATA_DIR, f"root_validation_{config_id}_history.json")


def register_candidates(candidates: list[Config]) -> None:
    """root_decider consulta esto para saber a qué candidatos, además del
    campeón, ofrecerles cada wallet-buy detectado — así acumulan muestra
    sin esperar su propio tráfico."""
    with _lock:
        for cfg in candidates:
            _active_candidates[cfg.config_id] = cfg


def active_candidates() -> list[Config]:
    with _lock:
        return list(_active_candidates.values())


def open_paper_position(config: Config, wallet_label: str, token_mint: str, entry_context: dict) -> None:
    root_sim.open_position(
        wallet_label, token_mint, entry_context,
        root_score=0, root_prob=0.0,
        config_id=config.config_id,
        stop_loss_pct=config.stop_loss_pct,
        max_hold_min=config.max_hold_min,
        trade_usd=config.trade_usd,
        balance_path=_balance_path(config.config_id),
        history_path=_history_path(config.config_id),
    )


def _read_history(config_id: str) -> list[dict]:
    path = _history_path(config_id)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _load_champion() -> Config | None:
    if not os.path.exists(CHAMPION_PATH):
        return None
    try:
        return config_from_dict(json.loads(open(CHAMPION_PATH).read()))
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def _save_champion(config: Config) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(CHAMPION_PATH, "w") as f:
        json.dump(config_to_dict(config), f, indent=2)


def promote_if_ready(candidate: Config, champion_history: list[dict]) -> bool:
    """Criterio de promoción: muestra mínima (PROMOTION_MIN_TRADES) en
    ambos lados, y el candidato tiene que ganarle al campeón incluso
    excluyendo el mejor trade de cada uno — así no se promueve por un solo
    outlier de suerte. Devuelve True si promovió."""
    cand_history = _read_history(candidate.config_id)
    if len(cand_history) < PROMOTION_MIN_TRADES or len(champion_history) < PROMOTION_MIN_TRADES:
        return False

    cand_pnls = [t["pnl_usd"] for t in cand_history]
    champ_pnls = [t["pnl_usd"] for t in champion_history]
    cand_excl = sum(cand_pnls) - max(cand_pnls)
    champ_excl = sum(champ_pnls) - max(champ_pnls)

    if cand_excl <= champ_excl:
        return False

    _save_champion(candidate)
    log.info(
        f"[root_validation_pool] {candidate.config_id} promovido a campeón "
        f"(pnl_excl=${cand_excl:.2f} vs ${champ_excl:.2f} del anterior, {len(cand_history)} trades)"
    )
    return True


def promote_candidates(candidates: list[Config], dataset: list[dict]) -> None:
    """Llamado desde el ciclo evolutivo: registra los candidatos para que
    root_decider empiece a ofrecerles trades, y revisa si alguno de los que
    ya estaban activos juntó muestra para promover. Si todavía no hay
    campeón (primer arranque), guarda el primer candidato tal cual."""
    register_candidates(candidates)
    champion = _load_champion()
    if champion is None:
        if candidates:
            _save_champion(candidates[0])
        return

    champion_history = _read_history(champion.config_id)
    for cfg in candidates:
        if cfg.config_id == champion.config_id:
            continue
        promote_if_ready(cfg, champion_history)

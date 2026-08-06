"""
Punto de entrada real de decisión para copytrade — reemplaza el rol de
root_shadow.py (que solo observaba). Cuando scorer.should_copy() detecta un
wallet-buy, esto decide COPIAR/SKIP usando la config campeón vigente (o la
neutra si todavía no hay campeón guardado) y, si decide copiar, abre la
posición de papel real. Además ofrece el mismo trade a los candidatos
activos en validación (root_validation_pool) para que acumulen muestra sin
esperar tráfico propio.

La decisión en sí (score_entry_context) es una cuenta liviana en memoria,
así que se calcula de forma síncrona y se devuelve ya mismo — el trabajo
pesado (abrir posiciones, tocar red vía DexScreener) se lanza en un thread
daemon aparte y nunca puede bloquear ni tumbar el camino real de trading.
"""
import json
import os
import threading

from copytrade import root_validation_pool
from copytrade.root_config import Config, config_from_dict, decide as config_decide, neutral_config
from utils.logger import get_logger

log = get_logger("root_decider")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CHAMPION_PATH = os.path.join(_DATA_DIR, "root_champion.json")

ENABLED = os.getenv("ROOT_DECIDER_ENABLED", "true").lower() == "true"


def load_champion() -> Config:
    if not os.path.exists(CHAMPION_PATH):
        return neutral_config()
    try:
        return config_from_dict(json.loads(open(CHAMPION_PATH).read()))
    except (json.JSONDecodeError, OSError, KeyError):
        return neutral_config()


def _run(wallet_label: str, entry_context: dict, token_mint: str | None, champion: Config, decision: str) -> None:
    log.info(f"[root_decider] {wallet_label} → {decision} (config={champion.config_id})")

    if decision == "COPIAR" and token_mint:
        from copytrade.root_sim import open_position
        try:
            open_position(
                wallet_label, token_mint, entry_context,
                root_score=0, root_prob=0.0,
                config_id=champion.config_id,
                stop_loss_pct=champion.stop_loss_pct,
                max_hold_min=champion.max_hold_min,
                trade_usd=champion.trade_usd,
            )
        except Exception as e:
            log.warning(f"[root_decider] {wallet_label}: no se pudo abrir posición del campeón — {e}")

    if not token_mint:
        return
    for candidate in root_validation_pool.active_candidates():
        try:
            if config_decide(candidate, wallet_label, entry_context) == "COPIAR":
                root_validation_pool.open_paper_position(candidate, wallet_label, token_mint, entry_context)
        except Exception as e:
            log.warning(f"[root_decider] candidato {candidate.config_id} falló — {e}")


def decide(wallet_label: str, entry_context: dict | None, token_mint: str | None = None) -> str:
    """Punto de entrada llamado desde scorer.should_copy()."""
    if not ENABLED or not entry_context:
        return "SKIP"

    champion = load_champion()
    decision = config_decide(champion, wallet_label, entry_context)
    threading.Thread(
        target=_run, args=(wallet_label, entry_context, token_mint, champion, decision), daemon=True,
    ).start()
    return decision

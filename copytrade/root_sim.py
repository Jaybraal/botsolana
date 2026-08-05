"""
Paper-trading propio de las decisiones de ROOT — independiente del simulador
de reglas que ya corre en simulator.py, al que este módulo NUNCA toca.

Por qué: el shadow-mode (root_shadow.py) solo compara decisiones. El usuario
pidió ver si CopyScorer es rentable de verdad, no solo si coincide con las
reglas. Este módulo abre una posición de papel cada vez que ROOT dice COPIAR
(sin importar qué digan las reglas) y la sigue hasta la salida con reglas de
riesgo simples y honestas — capital y balance 100% separados
(data/root_sim_balance.json, data/root_sim_history.json).

Tamaño de posición fijo en dólares (no % del balance): la investigación del
propio balance de las reglas (`sim_balance.json`, $20→$40k en 6 semanas)
mostró que sizing como % de un balance compuesto produce números que no se
podrían ejecutar en la realidad (posiciones de $400 sobre pools de $0 de
liquidez). Acá cada trade arriesga el mismo monto fijo — el balance resultante
es interpretable como "ganancia/pérdida real acumulada", no un artefacto de
compounding.

Corre en threads daemon aparte — no puede bloquear ni afectar el trading
real. Igual que root_shadow.py, cualquier error se loguea y se descarta ahí.
"""
import json
import os
import threading
import time

from config import HARD_STOP_LOSS_PCT
from utils.dexscreener import get_best_pair, get_pair_price
from utils.logger import get_logger

log = get_logger("root_sim")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
BALANCE_PATH = os.path.join(_DATA_DIR, "root_sim_balance.json")
HISTORY_PATH = os.path.join(_DATA_DIR, "root_sim_history.json")

ENABLED = os.getenv("ROOT_SIM_ENABLED", "true").lower() == "true"
INITIAL_BALANCE = float(os.getenv("ROOT_SIM_INITIAL_BALANCE", "1000"))
TRADE_USD = float(os.getenv("ROOT_SIM_TRADE_USD", "50"))
# Mismo stop-loss duro que ya usa el bot real (config.HARD_STOP_LOSS_PCT) —
# no se inventó un número nuevo para esta simulación.
STOP_LOSS_PCT = HARD_STOP_LOSS_PCT
MAX_HOLD_MIN = float(os.getenv("ROOT_SIM_MAX_HOLD_MIN", "30"))
POLL_INTERVAL_S = float(os.getenv("ROOT_SIM_POLL_INTERVAL_S", "15"))

_lock = threading.Lock()
_positions: dict[str, dict] = {}


def _decide_exit(entry_price, current_price, elapsed_min, stop_loss_pct, max_hold_min):
    """Función pura: decide si hay que cerrar la posición ahora. None = seguir
    esperando. No inventa una decisión si no hay precio disponible."""
    if not current_price or current_price <= 0:
        return None
    pnl_pct = (current_price - entry_price) / entry_price * 100
    if pnl_pct <= -stop_loss_pct:
        return {"reason": "stop_loss", "pnl_pct": pnl_pct}
    if elapsed_min >= max_hold_min:
        return {"reason": "max_hold", "pnl_pct": pnl_pct}
    return None


def _load_balance() -> dict:
    if not os.path.exists(BALANCE_PATH):
        return {"balance": INITIAL_BALANCE, "initial": INITIAL_BALANCE}
    try:
        return json.loads(open(BALANCE_PATH).read())
    except (json.JSONDecodeError, OSError):
        return {"balance": INITIAL_BALANCE, "initial": INITIAL_BALANCE}


def _save_balance(balance: dict) -> None:
    os.makedirs(os.path.dirname(BALANCE_PATH), exist_ok=True)
    balance["updated_at"] = time.strftime("%H:%M:%S %d/%m/%Y")
    with open(BALANCE_PATH, "w") as f:
        json.dump(balance, f, indent=2)


def _append_history(record: dict) -> None:
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


def _close_position(position: dict, exit_info: dict, current_price: float) -> dict:
    """Registra el resultado y actualiza el balance de papel de ROOT. Toda la
    persistencia pasa por acá — un solo punto guardado bajo `_lock`."""
    pnl_pct = exit_info["pnl_pct"]
    pnl_usd = position["amount_usd"] * pnl_pct / 100

    with _lock:
        balance = _load_balance()
        balance["balance"] = balance.get("balance", INITIAL_BALANCE) + pnl_usd
        _save_balance(balance)

        record = {
            "ts": time.time(),
            "wallet": position["wallet"],
            "token_mint": position["token_mint"],
            "entry_price": position["entry_price"],
            "exit_price": current_price,
            "amount_usd": position["amount_usd"],
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usd": round(pnl_usd, 4),
            "exit_reason": exit_info["reason"],
            "won": pnl_pct > 0,
            "hold_min": round((time.time() - position["opened_at"]) / 60, 2),
            "root_score": position["root_score"],
            "root_prob": position["root_prob"],
            "balance_after": balance["balance"],
            # Features del momento de entrada + resultado real (won/pnl_pct) —
            # con esto se puede reentrenar sobre los propios trades de ROOT
            # más adelante, no solo sobre el historial de BotSolana.
            "entry_context": position.get("entry_context"),
        }
        _append_history(record)

    log.info(
        f"[root_sim] {position['wallet']} → cierra {position['token_mint'][:8]}... "
        f"{'✅' if record['won'] else '❌'} {pnl_pct:+.1f}% (${pnl_usd:+.2f}) — {exit_info['reason']} "
        f"| balance=${balance['balance']:.2f}"
    )
    return record


def _monitor(position: dict) -> None:
    """Sondea el precio cada POLL_INTERVAL_S hasta que toque salir. Cualquier
    error de red se loguea y se reintenta en el siguiente ciclo — nunca deja
    la posición abierta para siempre sin más que el propio max_hold."""
    while True:
        time.sleep(POLL_INTERVAL_S)
        try:
            price = get_pair_price(position["pair_address"])
        except Exception as e:
            log.debug(f"[root_sim] {position['wallet']}: error leyendo precio — {e}")
            continue

        elapsed_min = (time.time() - position["opened_at"]) / 60
        exit_info = _decide_exit(position["entry_price"], price, elapsed_min, STOP_LOSS_PCT, MAX_HOLD_MIN)
        if exit_info:
            _close_position(position, exit_info, price)
            with _lock:
                _positions.pop(position["token_mint"], None)
            return


def open_position(wallet_label: str, token_mint: str, entry_context: dict | None, root_score: int, root_prob: float) -> None:
    """Punto de entrada llamado desde root_shadow.py cuando ROOT dice COPIAR.
    No bloquea: valida lo mínimo, registra la posición y lanza el monitoreo
    en un hilo daemon aparte."""
    if not ENABLED:
        return
    with _lock:
        if token_mint in _positions:
            return  # ya la estamos siguiendo, no duplicar
        balance = _load_balance()
        if balance.get("balance", INITIAL_BALANCE) <= 0:
            log.warning("[root_sim] balance de papel agotado — no se abren posiciones nuevas")
            return

    entry_price = (entry_context or {}).get("price_usd")
    if not entry_price:
        log.debug(f"[root_sim] {wallet_label}: sin price_usd en entry_context — no se puede simular")
        return

    pair = get_best_pair(token_mint)
    pair_address = (pair or {}).get("pairAddress")
    if not pair_address:
        log.debug(f"[root_sim] {wallet_label}: sin par en DexScreener para {token_mint[:8]}... — no se puede simular")
        return

    position = {
        "wallet": wallet_label,
        "token_mint": token_mint,
        "entry_price": entry_price,
        "pair_address": pair_address,
        "amount_usd": TRADE_USD,
        "opened_at": time.time(),
        "root_score": root_score,
        "root_prob": root_prob,
        "entry_context": entry_context,
    }
    with _lock:
        if token_mint in _positions:
            return
        _positions[token_mint] = position

    log.info(f"[root_sim] {wallet_label} → abre {token_mint[:8]}... a ${entry_price} (${TRADE_USD:.0f}, score={root_score})")
    threading.Thread(target=_monitor, args=(position,), daemon=True).start()

"""
Paper-trading propio de las decisiones de ROOT — independiente del simulador
de reglas que ya corre en simulator.py, al que este módulo NUNCA toca.

Sirve a dos consumidores: root_decider.py (el campeón vigente, que decide
de verdad qué se copia) y root_validation_pool.py (candidatos en prueba,
cada uno con su propio balance/historial separado por config_id). Por eso
balance/historial/parámetros de riesgo ya no son globals fijos — se pasan
por parámetro, con los globals de siempre como default.

Tamaño de posición fijo en dólares (no % del balance): la investigación del
propio balance de las reglas (`sim_balance.json`, $20→$40k en 6 semanas)
mostró que sizing como % de un balance compuesto produce números que no se
podrían ejecutar en la realidad. Acá cada trade arriesga el monto fijo de
su config — el balance resultante es interpretable como ganancia/pérdida
real acumulada, no un artefacto de compounding.

Corre en threads daemon aparte — no puede bloquear ni afectar el trading
real. Cualquier error se loguea y se descarta ahí.
"""
import json
import os
import threading
import time

from config import HARD_STOP_LOSS_PCT
from copytrade import root_trajectory
from utils.dexscreener import get_best_pair, get_pair_price
from utils.logger import get_logger

log = get_logger("root_sim")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
BALANCE_PATH = os.path.join(_DATA_DIR, "root_sim_balance.json")
HISTORY_PATH = os.path.join(_DATA_DIR, "root_sim_history.json")

ENABLED = os.getenv("ROOT_SIM_ENABLED", "true").lower() == "true"
INITIAL_BALANCE = float(os.getenv("ROOT_SIM_INITIAL_BALANCE", "1000"))
TRADE_USD = float(os.getenv("ROOT_SIM_TRADE_USD", "50"))
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


def _load_balance(balance_path: str) -> dict:
    if not os.path.exists(balance_path):
        return {"balance": INITIAL_BALANCE, "initial": INITIAL_BALANCE}
    try:
        return json.loads(open(balance_path).read())
    except (json.JSONDecodeError, OSError):
        return {"balance": INITIAL_BALANCE, "initial": INITIAL_BALANCE}


def _save_balance(balance_path: str, balance: dict) -> None:
    os.makedirs(os.path.dirname(balance_path), exist_ok=True)
    balance["updated_at"] = time.strftime("%H:%M:%S %d/%m/%Y")
    with open(balance_path, "w") as f:
        json.dump(balance, f, indent=2)


def _append_history(history_path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(history_path), exist_ok=True)
    with open(history_path, "a") as f:
        f.write(json.dumps(record) + "\n")


def _close_position(position: dict, exit_info: dict, current_price: float) -> dict:
    """Registra el resultado y actualiza el balance de papel del config_id
    de esta posición. Toda la persistencia pasa por acá, bajo `_lock`."""
    pnl_pct = exit_info["pnl_pct"]
    pnl_usd = position["amount_usd"] * pnl_pct / 100

    with _lock:
        balance = _load_balance(position["balance_path"])
        balance["balance"] = balance.get("balance", INITIAL_BALANCE) + pnl_usd
        _save_balance(position["balance_path"], balance)

        record = {
            "ts": time.time(),
            "wallet": position["wallet"],
            "token_mint": position["token_mint"],
            "config_id": position["config_id"],
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
            "entry_context": position.get("entry_context"),
        }
        _append_history(position["history_path"], record)

    root_trajectory.close_trajectory(position["token_mint"], position["wallet"], position["config_id"])

    log.info(
        f"[root_sim] {position['wallet']} ({position['config_id']}) → cierra {position['token_mint'][:8]}... "
        f"{'✅' if record['won'] else '❌'} {pnl_pct:+.1f}% (${pnl_usd:+.2f}) — {exit_info['reason']} "
        f"| balance=${balance['balance']:.2f}"
    )
    return record


def _monitor(position: dict) -> None:
    """Sondea el precio cada POLL_INTERVAL_S hasta que toque salir, grabando
    cada punto en root_trajectory para que el backtest pueda re-simular
    exits alternativos más adelante."""
    key = f"{position['config_id']}:{position['token_mint']}"
    while True:
        time.sleep(POLL_INTERVAL_S)
        try:
            price = get_pair_price(position["pair_address"])
        except Exception as e:
            log.debug(f"[root_sim] {position['wallet']}: error leyendo precio — {e}")
            continue

        elapsed_min = (time.time() - position["opened_at"]) / 60
        if price and price > 0:
            root_trajectory.record_snapshot(position["token_mint"], elapsed_min * 60, price)

        exit_info = _decide_exit(
            position["entry_price"], price, elapsed_min,
            position["stop_loss_pct"], position["max_hold_min"],
        )
        if exit_info:
            _close_position(position, exit_info, price)
            with _lock:
                _positions.pop(key, None)
            return


def open_position(
    wallet_label: str,
    token_mint: str,
    entry_context: dict | None,
    root_score: int,
    root_prob: float,
    config_id: str = "champion",
    stop_loss_pct: float | None = None,
    max_hold_min: float | None = None,
    trade_usd: float | None = None,
    balance_path: str | None = None,
    history_path: str | None = None,
) -> None:
    """Punto de entrada llamado desde root_decider.py (campeón) y
    root_validation_pool.py (candidatos). No bloquea: valida lo mínimo,
    registra la posición y lanza el monitoreo en un hilo daemon aparte.
    `_positions` se indexa por `config_id:token_mint` — el campeón y varios
    candidatos pueden seguir el mismo token en paralelo sin pisarse."""
    if not ENABLED:
        return

    stop_loss_pct = STOP_LOSS_PCT if stop_loss_pct is None else stop_loss_pct
    max_hold_min = MAX_HOLD_MIN if max_hold_min is None else max_hold_min
    trade_usd = TRADE_USD if trade_usd is None else trade_usd
    balance_path = balance_path or BALANCE_PATH
    history_path = history_path or HISTORY_PATH

    position_key = f"{config_id}:{token_mint}"
    with _lock:
        if position_key in _positions:
            return
        balance = _load_balance(balance_path)
        if balance.get("balance", INITIAL_BALANCE) <= 0:
            log.warning(f"[root_sim] balance de papel agotado ({config_id}) — no se abren posiciones nuevas")
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
        "config_id": config_id,
        "entry_price": entry_price,
        "pair_address": pair_address,
        "amount_usd": trade_usd,
        "opened_at": time.time(),
        "root_score": root_score,
        "root_prob": root_prob,
        "entry_context": entry_context,
        "stop_loss_pct": stop_loss_pct,
        "max_hold_min": max_hold_min,
        "balance_path": balance_path,
        "history_path": history_path,
    }
    with _lock:
        if position_key in _positions:
            return
        _positions[position_key] = position

    root_trajectory.start_trajectory(token_mint, entry_price)
    log.info(f"[root_sim] {wallet_label} ({config_id}) → abre {token_mint[:8]}... a ${entry_price} (${trade_usd:.0f})")
    threading.Thread(target=_monitor, args=(position,), daemon=True).start()

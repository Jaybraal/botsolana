"""Bitácora de señales ROOT para auditar qué evidencia entra al paper trading."""
import json
import os
import threading
import time

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LEDGER_PATH = os.path.join(_DATA_DIR, "root_signal_ledger.jsonl")
_lock = threading.Lock()


def record_signal(wallet_label: str, token_mint: str | None, entry_context: dict, decision: str, config_id: str) -> dict:
    """Registra la señal aunque sea rechazada; no realiza ninguna operación."""
    try:
        liquidity = float(entry_context.get("liquidity_usd", 0))
        price = float(entry_context.get("price_usd", 0))
    except (TypeError, ValueError):
        liquidity = price = 0.0
    record = {
        "ts": time.time(), "wallet": wallet_label, "token_mint": token_mint,
        "config_id": config_id, "decision": decision,
        "executable": bool(token_mint and price > 0 and liquidity > 0),
        "entry_context": entry_context,
    }
    with _lock:
        os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
        with open(LEDGER_PATH, "a") as file:
            file.write(json.dumps(record) + "\n")
    return record

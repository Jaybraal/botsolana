"""
Wrapper para PumpPortal API — ejecuta swaps en Pump.fun bonding curve.
Úsalo cuando Jupiter no puede rutear (token aún no graduado a AMM externo).
"""

import httpx
from utils.logger import get_logger

log = get_logger("pumpfun")

PUMPPORTAL_URL = "https://pumpportal.fun/api/trade-local"
DEFAULT_SLIPPAGE    = 15    # % — reducido para mejor ejecución
DEFAULT_PRIORITY    = 0.0002  # SOL — suficiente para confirmación rápida en Solana


def get_pump_buy_tx(pubkey: str, mint: str, amount_sol: float) -> bytes | None:
    """
    Pide a PumpPortal una TX para comprar `amount_sol` SOL del token `mint`.
    Retorna los bytes crudos de la VersionedTransaction firmable, o None si falla.
    """
    payload = {
        "publicKey":        pubkey,
        "action":           "buy",
        "mint":             mint,
        "denominatedInSol": "true",
        "amount":           round(amount_sol, 6),
        "slippage":         DEFAULT_SLIPPAGE,
        "priorityFee":      DEFAULT_PRIORITY,
        "pool":             "pump",
    }
    return _call_pumpportal(payload, f"buy {amount_sol:.5f} SOL → {mint[:8]}...")


def get_pump_sell_tx(pubkey: str, mint: str, amount_tokens: float, pool: str = "pump") -> bytes | None:
    """
    Pide a PumpPortal una TX para vender `amount_tokens` (unidades mínimas) del token `mint`.
    pool: "pump" para bonding curve, "pumpswap" para token graduado a PumpSwap AMM.
    Retorna los bytes crudos de la VersionedTransaction firmable, o None si falla.
    """
    payload = {
        "publicKey":        pubkey,
        "action":           "sell",
        "mint":             mint,
        "denominatedInSol": "false",
        "amount":           amount_tokens,
        "slippage":         DEFAULT_SLIPPAGE,
        "priorityFee":      DEFAULT_PRIORITY,
        "pool":             pool,
    }
    return _call_pumpportal(payload, f"sell ({pool}) {amount_tokens} tokens de {mint[:8]}...")


def _call_pumpportal(payload: dict, desc: str) -> bytes | None:
    try:
        log.debug(f"PumpPortal payload [{desc}]: {payload}")
        r = httpx.post(PUMPPORTAL_URL, json=payload, timeout=15)
        if r.status_code != 200:
            log.warning(f"PumpPortal [{desc}] HTTP {r.status_code} | payload: {payload} | resp: {r.text[:300]}")
            return None
        if not r.content:
            log.warning(f"PumpPortal [{desc}] respuesta vacía")
            return None
        log.debug(f"PumpPortal [{desc}] OK — {len(r.content)} bytes")
        return r.content
    except httpx.TimeoutException:
        log.warning(f"PumpPortal [{desc}] timeout")
        return None
    except Exception as e:
        log.warning(f"PumpPortal [{desc}] error: {e}")
        return None

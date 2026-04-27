"""
Wrapper de Jupiter API v6 para quotes y swaps.
"""

import httpx
import base64
import time
import socket
from utils.logger import get_logger
from config import JUPITER_QUOTE_URL, JUPITER_SWAP_URL, SLIPPAGE_BPS

# Railway containers a veces fallan DNS con IPv6 — forzar IPv4
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_only

log = get_logger("jupiter")

# URLs alternativas de Jupiter por si falla DNS del primario
_QUOTE_URLS = [
    JUPITER_QUOTE_URL,                          # quote-api.jup.ag/v6/quote
    "https://public.jupiterapi.com/quote",      # endpoint público alternativo
]
_SWAP_URLS = [
    JUPITER_SWAP_URL,                           # quote-api.jup.ag/v6/swap
    "https://public.jupiterapi.com/swap",
]


def get_quote(input_mint: str, output_mint: str, amount_lamports: int) -> dict | None:
    """
    Pide un quote a Jupiter para input_mint → output_mint.
    Intenta múltiples endpoints si hay fallo de DNS o timeout.
    """
    params = {
        "inputMint":        input_mint,
        "outputMint":       output_mint,
        "amount":           amount_lamports,
        "slippageBps":      SLIPPAGE_BPS,
        "onlyDirectRoutes": "false",
    }
    for url in _QUOTE_URLS:
        try:
            r = httpx.get(url, params=params, timeout=10)
            if r.status_code != 200:
                try:
                    err  = r.json()
                    code = err.get("errorCode", "")
                    msg  = err.get("error", r.text[:120])
                except Exception:
                    code, msg = "", r.text[:120]
                if code == "COULD_NOT_FIND_ANY_ROUTE":
                    log.warning(f"Jupiter sin ruta para {output_mint[:8]}... — token sin liquidez (bonding curve)")
                else:
                    log.warning(f"Jupiter quote HTTP {r.status_code} [{code}]: {msg}")
                return None
            return r.json()
        except httpx.TimeoutException:
            log.warning(f"Jupiter quote timeout en {url[:40]}...")
            continue
        except Exception as e:
            log.warning(f"Jupiter quote error ({url[:40]}...): {e}")
            continue
    return None


def get_swap_transaction(quote: dict, user_pubkey: str) -> str | None:
    """
    Obtiene la transacción serializada de Jupiter lista para firmar.
    Intenta múltiples endpoints si hay fallo de DNS o timeout.
    """
    body = {
        "quoteResponse":             quote,
        "userPublicKey":             user_pubkey,
        "wrapAndUnwrapSol":          True,
        "dynamicComputeUnitLimit":   True,
        "prioritizationFeeLamports": "auto",
    }
    for url in _SWAP_URLS:
        try:
            r = httpx.post(url, json=body, timeout=15)
            if r.status_code != 200:
                log.warning(f"Jupiter swap TX HTTP {r.status_code}: {r.text[:150]}")
                return None
            return r.json().get("swapTransaction")
        except httpx.TimeoutException:
            log.warning(f"Jupiter swap TX timeout ({url[:40]}...)")
            continue
        except Exception as e:
            log.warning(f"Jupiter swap TX error ({url[:40]}...): {e}")
            continue
    return None


def calc_price_impact(quote: dict) -> float:
    """Retorna el price impact % del quote."""
    return float(quote.get("priceImpactPct", 0)) * 100


def out_amount(quote: dict) -> int:
    return int(quote.get("outAmount", 0))

"""
Wrapper de Jupiter API v6 para quotes y swaps.
"""

import httpx
import base64
from config import JUPITER_QUOTE_URL, JUPITER_SWAP_URL, SLIPPAGE_BPS


def get_quote(input_mint: str, output_mint: str, amount_lamports: int) -> dict | None:
    """
    Pide un quote a Jupiter para input_mint → output_mint.
    amount_lamports: cantidad del token de entrada en unidades mínimas.
    """
    try:
        r = httpx.get(JUPITER_QUOTE_URL, params={
            "inputMint":        input_mint,
            "outputMint":       output_mint,
            "amount":           amount_lamports,
            "slippageBps":      SLIPPAGE_BPS,
            "onlyDirectRoutes": "false",
        }, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return None


def get_swap_transaction(quote: dict, user_pubkey: str) -> str | None:
    """
    Obtiene la transacción serializada de Jupiter lista para firmar.
    Retorna base64 o None si falla.
    """
    try:
        body = {
            "quoteResponse":             quote,
            "userPublicKey":             user_pubkey,
            "wrapAndUnwrapSol":          True,
            "dynamicComputeUnitLimit":   True,
            "prioritizationFeeLamports": "auto",
        }
        r = httpx.post(JUPITER_SWAP_URL, json=body, timeout=15)
        r.raise_for_status()
        return r.json().get("swapTransaction")
    except Exception as e:
        return None


def calc_price_impact(quote: dict) -> float:
    """Retorna el price impact % del quote."""
    return float(quote.get("priceImpactPct", 0)) * 100


def out_amount(quote: dict) -> int:
    return int(quote.get("outAmount", 0))

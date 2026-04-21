"""
Ejecuta compras y ventas para el sniper usando Jupiter API v6.
Reutiliza la lógica de utils/jupiter.py con slippage más alto para tokens nuevos.
"""

import base64
import base58
import httpx

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solana.rpc.api import Client

from config import (
    RPC_HTTP, WALLET_PUBKEY, WALLET_PRIVKEY,
    SNIPER_AMOUNT_USD, SNIPER_SLIPPAGE_BPS,
    JUPITER_QUOTE_URL, JUPITER_SWAP_URL,
    TOKENS,
)
from sniper import positions as pos_store
from utils.logger import get_logger

log    = get_logger("trader")
client = Client(RPC_HTTP)

USDC_MINT = TOKENS["USDC"]


def _load_keypair() -> Keypair | None:
    if not WALLET_PRIVKEY:
        return None
    try:
        secret = base58.b58decode(WALLET_PRIVKEY)
        return Keypair.from_bytes(secret)
    except Exception as e:
        log.error(f"Error cargando keypair: {e}")
        return None


def _quote(input_mint: str, output_mint: str, amount: int) -> dict | None:
    try:
        r = httpx.get(JUPITER_QUOTE_URL, params={
            "inputMint":   input_mint,
            "outputMint":  output_mint,
            "amount":      amount,
            "slippageBps": SNIPER_SLIPPAGE_BPS,
        }, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"Quote error: {e}")
        return None


def _swap_tx(quote: dict) -> str | None:
    try:
        r = httpx.post(JUPITER_SWAP_URL, json={
            "quoteResponse":             quote,
            "userPublicKey":             WALLET_PUBKEY,
            "wrapAndUnwrapSol":          True,
            "dynamicComputeUnitLimit":   True,
            "prioritizationFeeLamports": "auto",
        }, timeout=15)
        r.raise_for_status()
        return r.json().get("swapTransaction")
    except Exception as e:
        log.error(f"Swap TX error: {e}")
        return None


def _send_tx(tx_b64: str, keypair: Keypair) -> str | None:
    try:
        raw   = base64.b64decode(tx_b64)
        tx    = VersionedTransaction.from_bytes(raw)
        tx    = VersionedTransaction(tx.message, [keypair])
        resp  = client.send_raw_transaction(bytes(tx))
        sig   = str(resp.value)
        conf  = client.confirm_transaction(resp.value, commitment="confirmed")
        if conf.value:
            return sig
        log.error(f"TX no confirmada: {sig}")
        return None
    except Exception as e:
        log.error(f"Error enviando TX: {e}")
        return None


# ── API pública ──────────────────────────────────────────────────────────

def buy_token(opp: dict) -> bool:
    """
    Compra un token usando USDC como capital.
    opp: dict proveniente de scout.find_opportunities()
    """
    keypair = _load_keypair()
    token_address = opp["token_address"]
    symbol        = opp["symbol"]
    price_usd     = opp["price_usd"]

    if not keypair or not WALLET_PUBKEY:
        pos_store.open_position(
            token_address = token_address,
            pair_address  = opp["pair_address"],
            symbol        = symbol,
            entry_price   = price_usd,
            amount_usd    = SNIPER_AMOUNT_USD,
            mcap          = opp.get("mcap",       0),
            age_hours     = opp.get("age_hours",  0),
            dex           = opp.get("dex",        "?"),
            buys_5m       = opp.get("buys_5m",    0),
            change_5m     = opp.get("change_5m",  0),
            change_1h     = opp.get("change_1h",  0),
            change_6h     = opp.get("change_6h",  0),
            token_type    = opp.get("token_type", "runner"),
        )
        return True

    # USDC tiene 6 decimales
    usdc_amount = int(SNIPER_AMOUNT_USD * 1_000_000)

    log.info(f"Comprando {symbol} @ ${price_usd:.6f} | Gastando ${SNIPER_AMOUNT_USD} USDC...")

    quote = _quote(USDC_MINT, token_address, usdc_amount)
    if not quote:
        return False

    impact = float(quote.get("priceImpactPct", 0)) * 100
    if impact > 5.0:
        log.warning(f"Price impact muy alto en {symbol}: {impact:.2f}% — abortando compra")
        return False

    out_amount = int(quote.get("outAmount", 0))
    log.info(f"Quote OK: {usdc_amount/1e6:.2f} USDC → {out_amount} {symbol} | Impact {impact:.2f}%")

    tx_b64 = _swap_tx(quote)
    if not tx_b64:
        return False

    sig = _send_tx(tx_b64, keypair)
    if not sig:
        return False

    log.info(f"COMPRA OK: {symbol} | TX: {sig} | https://solscan.io/tx/{sig}")

    pos_store.open_position(
        token_address = token_address,
        pair_address  = opp["pair_address"],
        symbol        = symbol,
        entry_price   = price_usd,
        amount_usd    = SNIPER_AMOUNT_USD,
        mcap          = opp.get("mcap",      0),
        age_hours     = opp.get("age_hours", 0),
        dex           = opp.get("dex",       "?"),
        buys_5m       = opp.get("buys_5m",   0),
        change_5m     = opp.get("change_5m", 0),
        change_1h     = opp.get("change_1h",  0),
        change_6h     = opp.get("change_6h",  0),
        token_type    = opp.get("token_type", "runner"),
    )
    return True


def sell_token(signal) -> bool:
    """
    Vende un token de vuelta a USDC.
    signal: ExitSignal de positions.check_exits()
    """
    positions = pos_store.get_all()
    pos = positions.get(signal.token_address)
    if not pos:
        return False

    symbol       = pos["symbol"]
    token_amount = pos.get("token_amount", 0)
    keypair      = _load_keypair()

    if not keypair or not WALLET_PUBKEY or token_amount == 0:
        # Modo simulación
        pos_store.close_position(signal.token_address, signal.reason, signal.current_price)
        return True

    log.info(f"Vendiendo {symbol} | Razón: {signal.reason} | Precio: ${signal.current_price:.6f}")

    quote = _quote(signal.token_address, USDC_MINT, token_amount)
    if not quote:
        log.error(f"No se pudo cotizar venta de {symbol}")
        return False

    tx_b64 = _swap_tx(quote)
    if not tx_b64:
        return False

    sig = _send_tx(tx_b64, keypair)
    if not sig:
        return False

    log.info(f"VENTA OK: {symbol} | TX: {sig} | https://solscan.io/tx/{sig}")
    pos_store.close_position(signal.token_address, signal.reason, signal.current_price)
    return True

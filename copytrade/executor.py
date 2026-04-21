"""
Ejecuta el copy trade usando Jupiter API.
Recibe el swap detectado, pide quote y envía la tx.
"""

import base64
import json
import os
import time
import httpx
from datetime import datetime
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solana.rpc.api import Client

from config import RPC_HTTP, WALLET_PUBKEY, WALLET_PRIVKEY, TRADE_AMOUNT_USD, SLIPPAGE_BPS
from utils.jupiter import get_quote, get_swap_transaction, calc_price_impact, out_amount
from utils.logger import get_logger

log    = get_logger("executor")
client = Client(RPC_HTTP)

os.makedirs("data", exist_ok=True)
COPYTRADES_FILE = "data/copytrades.json"


def _append_copytrade(entry: dict):
    data = []
    if os.path.exists(COPYTRADES_FILE):
        try:
            with open(COPYTRADES_FILE) as f:
                data = json.load(f)
        except Exception:
            pass
    data.append(entry)
    with open(COPYTRADES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_keypair() -> Keypair | None:
    if not WALLET_PRIVKEY:
        return None
    try:
        import base58
        secret = base58.b58decode(WALLET_PRIVKEY)
        return Keypair.from_bytes(secret)
    except Exception as e:
        log.error(f"Error cargando keypair: {e}")
        return None


def get_token_decimals_approx(mint: str) -> int:
    """Decimales aproximados — USDC/USDT = 6, resto = 9."""
    stable_mints = {
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 6,  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": 6,  # USDT
    }
    return stable_mints.get(mint, 9)


def execute_copy(swap: dict) -> bool:
    """
    Ejecuta un copy del swap detectado.
    Usa TRADE_AMOUNT_USD como capital propio (no copia el monto exacto).
    Los retiros/transferencias simples de SOL ya son filtrados por el decoder
    (solo llega aquí si la tx pasó por un programa DEX conocido).
    """
    keypair = load_keypair()
    label = swap.get("wallet_label", f"{swap['wallet'][:8]}...")

    if not keypair:
        entry = {
            "timestamp":    time.time(),
            "time_str":     datetime.now().strftime("%H:%M:%S %d/%m"),
            "wallet":       swap["wallet"],
            "wallet_label": label,
            "program":      swap["program"],
            "symbol_in":    swap["symbol_in"],
            "symbol_out":   swap["symbol_out"],
            "token_in":     swap["token_in"],
            "token_out":    swap["token_out"],
            "amount_in":    swap["amount_in"],
            "simulated":    True,
        }
        _append_copytrade(entry)
        log.info(
            f"[SIM] [bold cyan]{label}[/] | "
            f"[yellow]{swap['symbol_in']}[/] → [green]{swap['symbol_out']}[/] "
            f"via [white]{swap['program']}[/]"
        )
        return True

    if not WALLET_PUBKEY:
        log.warning("WALLET_PUBKEY no configurado.")
        return False

    token_in  = swap["token_in"]
    token_out = swap["token_out"]

    # Calcular amount en unidades mínimas según TRADE_AMOUNT_USD
    decimals   = get_token_decimals_approx(token_in)
    amount_raw = int(TRADE_AMOUNT_USD * (10 ** decimals))

    log.info(
        f"[COPY] [bold cyan]{label}[/] hizo {swap['symbol_in']}→{swap['symbol_out']} "
        f"en {swap['program']} | Replicando ${TRADE_AMOUNT_USD}..."
    )

    # 1. Quote
    quote = get_quote(token_in, token_out, amount_raw)
    if not quote:
        log.error("No se pudo obtener quote de Jupiter.")
        return False

    impact = calc_price_impact(quote)
    if impact > 3.0:
        log.warning(f"Price impact muy alto ({impact:.2f}%) — abortando.")
        return False

    expected_out = out_amount(quote)
    log.info(f"Quote: {amount_raw} {swap['symbol_in']} → {expected_out} {swap['symbol_out']} | Impact: {impact:.3f}%")

    # 2. Obtener tx serializada
    swap_tx_b64 = get_swap_transaction(quote, WALLET_PUBKEY)
    if not swap_tx_b64:
        log.error("No se pudo obtener swap transaction de Jupiter.")
        return False

    # 3. Firmar
    try:
        raw_bytes = base64.b64decode(swap_tx_b64)
        tx        = VersionedTransaction.from_bytes(raw_bytes)
        tx_signed = VersionedTransaction(tx.message, [keypair])
        tx_bytes  = bytes(tx_signed)
    except Exception as e:
        log.error(f"Error firmando tx: {e}")
        return False

    # 4. Enviar
    try:
        resp = client.send_raw_transaction(tx_bytes)
        sig  = str(resp.value)
        log.info(f"TX enviada: {sig}")
        log.info(f"Ver en: https://solscan.io/tx/{sig}")

        # Confirmar
        conf = client.confirm_transaction(resp.value, commitment="confirmed")
        if conf.value:
            log.info(f"COPY TRADE OK — {swap['symbol_in']}→{swap['symbol_out']} | TX: {sig}")
            _append_copytrade({
                "timestamp":    time.time(),
                "time_str":     datetime.now().strftime("%H:%M:%S %d/%m"),
                "wallet":       swap["wallet"],
                "wallet_label": label,
                "program":      swap["program"],
                "symbol_in":    swap["symbol_in"],
                "symbol_out":   swap["symbol_out"],
                "token_in":     swap["token_in"],
                "token_out":    swap["token_out"],
                "amount_usd":   TRADE_AMOUNT_USD,
                "tx_sig":       sig,
                "simulated":    False,
            })
            return True
        else:
            log.error(f"TX no confirmada: {sig}")
            return False

    except Exception as e:
        log.error(f"Error enviando tx: {e}")
        return False

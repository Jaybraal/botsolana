"""
Ejecuta el copy trade usando Jupiter API.
Modo proporcional: invierte el mismo % del capital que la wallet objetivo.
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
from solana.rpc.types import TokenAccountOpts

from config import (
    RPC_HTTP, WALLET_PUBKEY, WALLET_PRIVKEY, SLIPPAGE_BPS,
    PROPORTIONAL_MODE, MAX_TRADE_PCT, MIN_TRADE_SOL, MAX_OPEN_COPIES,
    STOP_LOSS_PCT, MIN_RESERVE_SOL, MAX_PRICE_IMPACT, SCALING_TIERS,
    TOKENS,
)
from utils.jupiter import get_quote, get_swap_transaction, calc_price_impact, out_amount
from utils.logger import get_logger
from copytrade import simulator

log    = get_logger("executor")
client = Client(RPC_HTTP)

os.makedirs("data", exist_ok=True)
COPYTRADES_FILE = "data/copytrades.json"

SOL_MINT     = TOKENS["SOL"]
LAMPORTS_PER_SOL = 1_000_000_000

# Tracking en memoria de posiciones abiertas: {token_mint: {"symbol": str, "opened": float}}
_open_copies: dict[str, dict] = {}

# Balance inicial de SOL (lamports) — se registra en el primer trade en vivo
_initial_balance: int = 0


def _ensure_initial_balance():
    """Registra el balance inicial una sola vez, al primer trade en vivo."""
    global _initial_balance
    if _initial_balance == 0 and WALLET_PUBKEY:
        bal = get_our_sol_balance()
        if bal > 0:
            _initial_balance = bal
            log.info(
                f"[CAPITAL] Balance inicial registrado: "
                f"[bold white]{bal / LAMPORTS_PER_SOL:.4f} SOL[/] | "
                f"Stop-loss activo si cae bajo "
                f"[bold red]{bal * STOP_LOSS_PCT / LAMPORTS_PER_SOL:.4f} SOL[/] "
                f"({STOP_LOSS_PCT*100:.0f}%)"
            )


def _is_stop_loss_triggered(current_balance: int) -> bool:
    """Retorna True si el balance cayó por debajo del umbral de stop-loss."""
    if _initial_balance == 0:
        return False
    threshold = int(_initial_balance * STOP_LOSS_PCT)
    if current_balance < threshold:
        log.warning(
            f"[bold red][STOP-LOSS ACTIVO][/] Balance "
            f"{current_balance / LAMPORTS_PER_SOL:.4f} SOL < "
            f"umbral {threshold / LAMPORTS_PER_SOL:.4f} SOL — "
            f"trading pausado para proteger capital"
        )
        return True
    return False


def _get_dynamic_trade_pct(current_balance: int) -> float:
    """
    Retorna el % máximo por trade según la ganancia acumulada sobre el capital inicial.
    Cuanto más ganás, el techo por trade sube — pero siempre usando las ganancias,
    no el capital base de $20.
    """
    if _initial_balance == 0:
        return MAX_TRADE_PCT
    profit_pct = (current_balance - _initial_balance) / _initial_balance
    # Recorrer tiers de mayor a menor y devolver el primero que aplique
    for min_profit, trade_pct in reversed(SCALING_TIERS):
        if profit_pct >= min_profit:
            if trade_pct > MAX_TRADE_PCT:
                log.info(
                    f"[ESCALADO] Ganancia acumulada: [green]{profit_pct*100:+.1f}%[/] → "
                    f"techo por trade: [bold white]{trade_pct*100:.0f}%[/] "
                    f"(base era {MAX_TRADE_PCT*100:.0f}%)"
                )
            return trade_pct
    return MAX_TRADE_PCT


# ── Persistencia ─────────────────────────────────────────────────────────────

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


# ── Wallet / balance ──────────────────────────────────────────────────────────

def load_keypair() -> Keypair | None:
    if not WALLET_PRIVKEY:
        return None
    try:
        import base58
        return Keypair.from_bytes(base58.b58decode(WALLET_PRIVKEY))
    except Exception as e:
        log.error(f"Error cargando keypair: {e}")
        return None


def get_our_sol_balance() -> int:
    """Retorna nuestro balance de SOL en lamports, o 0 si falla."""
    if not WALLET_PUBKEY:
        return 0
    try:
        resp = client.get_balance(WALLET_PUBKEY)
        return resp.value
    except Exception as e:
        log.error(f"Error obteniendo balance SOL: {e}")
        return 0


def get_our_token_balance(mint: str) -> int:
    """Retorna nuestro balance de un token en unidades mínimas, o 0 si no tenemos."""
    if not WALLET_PUBKEY:
        return 0
    try:
        opts = TokenAccountOpts(mint=mint)
        resp = client.get_token_accounts_by_owner_json_parsed(WALLET_PUBKEY, opts)
        accounts = resp.value
        if not accounts:
            return 0
        total = 0
        for acc in accounts:
            info = acc.account.data.parsed["info"]["tokenAmount"]
            total += int(info["amount"])
        return total
    except Exception as e:
        log.error(f"Error obteniendo balance token {mint[:8]}...: {e}")
        return 0


# ── Cálculo de monto proporcional ────────────────────────────────────────────

def calc_proportional_amount(swap: dict, our_balance_lamports: int) -> int | None:
    """
    Calcula cuántos lamports de SOL invertir, proporcional a lo que invirtió la wallet.

    Retorna lamports a invertir, o None si no se debe operar
    (mínimo no alcanzado, máximo de posiciones, etc.).
    """
    wallet_pre_sol = swap.get("wallet_pre_sol", 0)

    # Techo dinámico: sube si hay ganancias acumuladas
    dynamic_max = _get_dynamic_trade_pct(our_balance_lamports)

    if not PROPORTIONAL_MODE or wallet_pre_sol <= 0:
        # Fallback: usar el techo dinámico actual
        proportion = dynamic_max
    else:
        # Proporción real: cuánto % de su balance metió la wallet
        amount_in_sol = swap["amount_in"]  # lamports de SOL (token_in = SOL)
        proportion    = amount_in_sol / wallet_pre_sol
        proportion    = min(proportion, dynamic_max)  # tope dinámico

    our_amount = int(our_balance_lamports * proportion)

    # Mínimo absoluto
    min_lamports = int(MIN_TRADE_SOL * LAMPORTS_PER_SOL)
    if our_amount < min_lamports:
        log.warning(
            f"Trade proporcional muy pequeño ({our_amount / LAMPORTS_PER_SOL:.5f} SOL < "
            f"{MIN_TRADE_SOL} SOL mínimo) — ignorando"
        )
        return None

    return our_amount


# ── Execute ───────────────────────────────────────────────────────────────────

def execute_copy(swap: dict) -> bool:
    """
    Ejecuta un copy del swap detectado en modo proporcional.

    - COMPRA (SOL→token): proporcional al % que metió la wallet.
    - VENTA  (token→SOL): vende todo el balance que tengamos de ese token.
    """
    keypair = load_keypair()
    label   = swap.get("wallet_label", f"{swap['wallet'][:8]}...")
    is_buy  = swap["token_in"] == SOL_MINT
    is_sell = swap["token_out"] == SOL_MINT

    # ── Modo simulación ──────────────────────────────────────────────────────
    if not keypair:
        _simulate(swap, label, is_buy)
        return True

    if not WALLET_PUBKEY:
        log.warning("WALLET_PUBKEY no configurado.")
        return False

    # ── Compra ───────────────────────────────────────────────────────────────
    if is_buy:
        token_out = swap["token_out"]

        # No abrir más posiciones si ya estamos al límite
        if len(_open_copies) >= MAX_OPEN_COPIES:
            log.warning(
                f"[{label}] Límite de {MAX_OPEN_COPIES} posiciones abiertas alcanzado — "
                f"ignorando compra de {swap['symbol_out']}"
            )
            return False

        # No comprar el mismo token dos veces
        if token_out in _open_copies:
            log.warning(f"[{label}] Ya tenemos {swap['symbol_out']} abierto — ignorando entrada adicional")
            return False

        our_balance = get_our_sol_balance()
        if our_balance == 0:
            log.error("No se pudo obtener balance SOL propio.")
            return False

        # Registrar capital inicial (sólo la primera vez)
        _ensure_initial_balance()

        # Stop-loss global: parar si perdimos demasiado
        if _is_stop_loss_triggered(our_balance):
            return False

        amount_lamports = calc_proportional_amount(swap, our_balance)
        if amount_lamports is None:
            return False

        # Reserva mínima: nunca dejar el balance por debajo de MIN_RESERVE_SOL
        min_reserve_lamports = int(MIN_RESERVE_SOL * LAMPORTS_PER_SOL)
        available_lamports = our_balance - min_reserve_lamports
        if available_lamports <= 0:
            log.warning(
                f"[{label}] Balance ({our_balance / LAMPORTS_PER_SOL:.4f} SOL) "
                f"no supera la reserva mínima ({MIN_RESERVE_SOL} SOL) — ignorando"
            )
            return False
        if amount_lamports > available_lamports:
            log.info(
                f"[{label}] Monto reducido de {amount_lamports / LAMPORTS_PER_SOL:.4f} "
                f"a {available_lamports / LAMPORTS_PER_SOL:.4f} SOL para respetar reserva mínima"
            )
            amount_lamports = available_lamports

        proportion_pct = amount_lamports / our_balance * 100
        log.info(
            f"[COPY BUY] [bold cyan]{label}[/] | {swap['symbol_out']} | "
            f"Proporción: {proportion_pct:.1f}% | "
            f"Monto: {amount_lamports / LAMPORTS_PER_SOL:.4f} SOL | "
            f"Balance: {our_balance / LAMPORTS_PER_SOL:.3f} SOL"
        )

        sig = _send_swap(swap["token_in"], token_out, amount_lamports, keypair)
        if not sig:
            return False

        _open_copies[token_out] = {"symbol": swap["symbol_out"], "opened": time.time()}
        _append_copytrade({
            "timestamp":    time.time(),
            "time_str":     datetime.now().strftime("%H:%M:%S %d/%m"),
            "type":         "buy",
            "wallet":       swap["wallet"],
            "wallet_label": label,
            "program":      swap["program"],
            "symbol_in":    swap["symbol_in"],
            "symbol_out":   swap["symbol_out"],
            "token_in":     swap["token_in"],
            "token_out":    token_out,
            "amount_sol":   amount_lamports / LAMPORTS_PER_SOL,
            "proportion_pct": round(proportion_pct, 2),
            "tx_sig":       sig,
            "simulated":    False,
        })
        log.info(f"[bold green]COPY BUY OK[/] — {swap['symbol_out']} | TX: {sig}")
        # Alimentar el simulador en vivo para acumular datos de aprendizaje
        simulator.process(swap)
        return True

    # ── Venta ────────────────────────────────────────────────────────────────
    elif is_sell:
        token_in = swap["token_in"]

        # Solo vendemos si tenemos ese token en posición abierta
        if token_in not in _open_copies:
            log.debug(f"[{label}] Venta de {swap['symbol_in']} ignorada — no tenemos posición abierta")
            return False

        our_token_balance = get_our_token_balance(token_in)
        if our_token_balance == 0:
            log.warning(f"[{label}] Venta de {swap['symbol_in']} — balance propio es 0, nada que vender")
            _open_copies.pop(token_in, None)
            return False

        log.info(
            f"[COPY SELL] [bold cyan]{label}[/] | {swap['symbol_in']}→SOL | "
            f"Vendiendo todo: {our_token_balance} unidades"
        )

        sig = _send_swap(token_in, SOL_MINT, our_token_balance, keypair)
        if not sig:
            return False

        pos = _open_copies.pop(token_in, {})
        hold_min = (time.time() - pos.get("opened", time.time())) / 60
        _append_copytrade({
            "timestamp":    time.time(),
            "time_str":     datetime.now().strftime("%H:%M:%S %d/%m"),
            "type":         "sell",
            "wallet":       swap["wallet"],
            "wallet_label": label,
            "program":      swap["program"],
            "symbol_in":    swap["symbol_in"],
            "symbol_out":   swap["symbol_out"],
            "token_in":     token_in,
            "token_out":    SOL_MINT,
            "hold_min":     round(hold_min, 1),
            "tx_sig":       sig,
            "simulated":    False,
        })
        log.info(f"[bold green]COPY SELL OK[/] — {swap['symbol_in']} | Hold: {hold_min:.1f} min | TX: {sig}")
        # Alimentar el simulador en vivo para acumular datos de aprendizaje
        simulator.process(swap)
        return True

    else:
        # Token→token: ignorar, demasiado complejo de proporcionar sin precio
        log.debug(f"[{label}] Swap token→token ignorado ({swap['symbol_in']}→{swap['symbol_out']})")
        return False


# ── Enviar swap via Jupiter ───────────────────────────────────────────────────

def _send_swap(token_in: str, token_out: str, amount: int, keypair: Keypair) -> str | None:
    """Pide quote a Jupiter, firma y envía. Retorna la signature o None."""
    quote = get_quote(token_in, token_out, amount)
    if not quote:
        log.error("No se pudo obtener quote de Jupiter.")
        return None

    impact = calc_price_impact(quote)
    if impact > MAX_PRICE_IMPACT:
        log.warning(f"Price impact muy alto ({impact:.2f}% > {MAX_PRICE_IMPACT}%) — abortando.")
        return None

    swap_tx_b64 = get_swap_transaction(quote, WALLET_PUBKEY)
    if not swap_tx_b64:
        log.error("No se pudo obtener swap transaction de Jupiter.")
        return None

    try:
        raw_bytes = base64.b64decode(swap_tx_b64)
        tx        = VersionedTransaction.from_bytes(raw_bytes)
        tx_signed = VersionedTransaction(tx.message, [keypair])
        resp      = client.send_raw_transaction(bytes(tx_signed))
        sig       = str(resp.value)
        conf      = client.confirm_transaction(resp.value, commitment="confirmed")
        return sig if conf.value else None
    except Exception as e:
        log.error(f"Error enviando TX: {e}")
        return None


# ── Simulación ────────────────────────────────────────────────────────────────

def _simulate(swap: dict, label: str, is_buy: bool):
    """Registra el swap en modo simulación y calcula la proporción teórica."""
    wallet_pre_sol = swap.get("wallet_pre_sol", 0)

    if is_buy and wallet_pre_sol > 0:
        proportion = min(swap["amount_in"] / wallet_pre_sol, MAX_TRADE_PCT)
        prop_str   = f"{proportion * 100:.1f}%"
    else:
        proportion = None
        prop_str   = "—"

    entry = {
        "timestamp":    time.time(),
        "time_str":     datetime.now().strftime("%H:%M:%S %d/%m"),
        "type":         "buy" if is_buy else ("sell" if swap["token_out"] == SOL_MINT else "token-token"),
        "wallet":       swap["wallet"],
        "wallet_label": label,
        "program":      swap["program"],
        "symbol_in":    swap["symbol_in"],
        "symbol_out":   swap["symbol_out"],
        "token_in":     swap["token_in"],
        "token_out":    swap["token_out"],
        "amount_in":    swap["amount_in"],
        "proportion":   prop_str,
        "simulated":    True,
    }
    _append_copytrade(entry)
    log.info(
        f"[SIM] [bold cyan]{label}[/] | "
        f"[yellow]{swap['symbol_in']}[/] → [green]{swap['symbol_out']}[/] "
        f"via [white]{swap['program']}[/] | "
        f"Proporción: [white]{prop_str}[/]"
    )
    simulator.process(swap)

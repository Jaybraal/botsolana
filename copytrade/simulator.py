"""
Simulador de P&L para copy trades.

Cuando una wallet monitorizada compra un token → abrimos posición simulada.
Cuando la misma wallet vende ese token → cerramos y calculamos ganancia/pérdida.

El precio de entrada/salida se obtiene de DexScreener en el momento del swap.
Posiciones e historial persistidos en data/ para sobrevivir reinicios.
"""

import json
import os
import time
from datetime import datetime
from utils.dexscreener import get_best_pair
from utils.logger import get_logger

log = get_logger("simulator")

os.makedirs("data", exist_ok=True)
POSITIONS_FILE = "data/sim_positions.json"
HISTORY_FILE   = "data/sim_history.json"


def _load_positions() -> dict:
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _load_history() -> list:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_positions():
    with open(POSITIONS_FILE, "w") as f:
        json.dump(_positions, f, indent=2)


def _save_history():
    with open(HISTORY_FILE, "w") as f:
        json.dump(_history, f, indent=2)


# {wallet_address: {token_mint: posicion}}
_positions: dict[str, dict[str, dict]] = _load_positions()

# Historial de trades cerrados
_history: list[dict] = _load_history()

# Capital simulado por trade (igual que TRADE_AMOUNT_USD)
TRADE_AMOUNT_USD = 50.0

# Tokens que son "dinero" — cuando el token_out es uno de estos, es una COMPRA
# Cuando el token_in es uno de estos, es una VENTA
STABLE_MINTS = {
    "So11111111111111111111111111111111111111112",   # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", # USDT
}


def _get_price(mint: str) -> float | None:
    """Precio actual del token en USD via DexScreener."""
    pair = get_best_pair(mint)
    if not pair:
        return None
    try:
        return float(pair.get("priceUsd") or 0) or None
    except (ValueError, TypeError):
        return None


def process(swap: dict):
    """
    Analiza un swap y actualiza posiciones simuladas.
    Llamar desde executor.execute_copy() después de registrar el swap.
    """
    wallet       = swap.get("wallet", "")
    wallet_label = swap.get("wallet_label", wallet[:8] + "...")
    token_in     = swap.get("token_in",  "")
    token_out    = swap.get("token_out", "")
    symbol_in    = swap.get("symbol_in",  "?")
    symbol_out   = swap.get("symbol_out", "?")

    is_buy  = token_in  in STABLE_MINTS and token_out not in STABLE_MINTS
    is_sell = token_out in STABLE_MINTS and token_in  not in STABLE_MINTS

    if is_buy:
        _handle_buy(wallet, wallet_label, token_out, symbol_out)

    elif is_sell:
        _handle_sell(wallet, wallet_label, token_in, symbol_in)


def _handle_buy(wallet: str, label: str, token_mint: str, symbol: str):
    """Abre posición simulada al precio actual."""
    # Si ya tenemos posición en este token para esta wallet, ignorar
    if _positions.get(wallet, {}).get(token_mint):
        return

    price = _get_price(token_mint)
    if not price:
        log.debug(f"[SIM] No hay precio para {symbol} — no se abre posición")
        return

    pos = {
        "token_mint":  token_mint,
        "symbol":      symbol,
        "entry_price": price,
        "amount_usd":  TRADE_AMOUNT_USD,
        "opened_at":   time.time(),
        "opened_str":  datetime.now().strftime("%H:%M:%S %d/%m"),
        "wallet":      wallet,
        "wallet_label": label,
    }

    _positions.setdefault(wallet, {})[token_mint] = pos
    _save_positions()

    log.info(
        f"[SIM] 📥 ENTRADA | [cyan]{label}[/] compró [yellow]{symbol}[/] | "
        f"precio entrada: [white]${price:.8f}[/] | capital simulado: [green]${TRADE_AMOUNT_USD}[/]"
    )


def _handle_sell(wallet: str, label: str, token_mint: str, symbol: str):
    """Cierra posición simulada y calcula P&L."""
    pos = _positions.get(wallet, {}).pop(token_mint, None)
    if not pos:
        # La wallet está vendiendo algo que no copiamos en esta sesión
        log.debug(f"[SIM] Venta de {symbol} sin posición abierta — ignorando")
        return

    price_exit = _get_price(token_mint)
    if not price_exit:
        log.debug(f"[SIM] No hay precio de salida para {symbol} — no se cierra")
        _positions.setdefault(wallet, {})[token_mint] = pos  # restituir
        return

    entry      = pos["entry_price"]
    amount_usd = pos["amount_usd"]
    pnl_pct    = (price_exit - entry) / entry * 100
    pnl_usd    = amount_usd * pnl_pct / 100
    hold_min   = (time.time() - pos["opened_at"]) / 60
    won        = pnl_pct > 0

    trade = {
        "symbol":       symbol,
        "token":        token_mint,
        "wallet":       wallet,
        "wallet_label": label,
        "entry_price":  entry,
        "exit_price":   price_exit,
        "pnl_pct":      round(pnl_pct, 2),
        "pnl_usd":      round(pnl_usd, 2),
        "amount_usd":   amount_usd,
        "hold_min":     round(hold_min, 1),
        "won":          won,
        "opened_str":   pos["opened_str"],
        "closed_str":   datetime.now().strftime("%H:%M:%S %d/%m"),
        "timestamp":    time.time(),
    }
    _history.append(trade)
    _save_positions()
    _save_history()

    icon  = "[bold green]✅ WIN [/]" if won else "[bold red]❌ LOSS[/]"
    color = "green" if won else "red"

    log.info(
        f"[SIM] {icon} | [cyan]{label}[/] vendió [yellow]{symbol}[/] | "
        f"[{color}]{pnl_pct:+.1f}%[/] ([{color}]${pnl_usd:+.2f}[/]) | "
        f"entrada ${entry:.8f} → salida ${price_exit:.8f} | "
        f"{hold_min:.0f} min en posición"
    )

    _print_summary()


def _print_summary():
    """Muestra resumen acumulado en los logs."""
    if not _history:
        return
    wins     = [t for t in _history if t["won"]]
    losses   = [t for t in _history if not t["won"]]
    pnl_total = sum(t["pnl_usd"] for t in _history)
    win_rate  = len(wins) / len(_history) * 100

    log.info(
        f"[SIM] 📊 RESUMEN | "
        f"Trades: [white]{len(_history)}[/] | "
        f"Win rate: [{'green' if win_rate >= 50 else 'red'}]{win_rate:.0f}%[/] "
        f"([green]{len(wins)}W[/]/[red]{len(losses)}L[/]) | "
        f"P&L total: [{'green' if pnl_total >= 0 else 'red'}]${pnl_total:+.2f}[/]"
    )


def get_summary() -> dict:
    """Devuelve el resumen actual para mostrar en UI."""
    wins      = [t for t in _history if t["won"]]
    pnl_total = sum(t["pnl_usd"] for t in _history)
    open_pos  = sum(len(v) for v in _positions.values())
    return {
        "total_trades":   len(_history),
        "wins":           len(wins),
        "losses":         len(_history) - len(wins),
        "win_rate":       len(wins) / len(_history) * 100 if _history else 0,
        "pnl_total_usd":  round(pnl_total, 2),
        "open_positions": open_pos,
        "history":        _history[-20:],
    }

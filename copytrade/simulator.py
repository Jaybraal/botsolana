"""
Simulador de P&L para copy trades — modo capital proporcional y compuesto.

Cuando una wallet monitorizada compra un token → abre posición simulada.
Cuando la misma wallet vende ese token → cierra y calcula ganancia/pérdida.

El capital NO es fijo: empieza en SIM_CAPITAL ($20) y crece/baja con cada trade.
Cada compra usa SIM_TRADE_PCT del balance actual (con escalado igual al executor).
Así el simulador refleja exactamente lo que pasará con dinero real.
"""

import json
import os
import time
from datetime import datetime
from utils.dexscreener import get_best_pair
from utils.market_context import get_context
from utils.logger import get_logger
from config import SCALING_TIERS

log = get_logger("simulator")

os.makedirs("data", exist_ok=True)
POSITIONS_FILE = "data/sim_positions.json"
HISTORY_FILE   = "data/sim_history.json"
BALANCE_FILE   = "data/sim_balance.json"

# Capital inicial configurado en .env (default $20)
SIM_INITIAL_CAPITAL = float(os.getenv("SIM_CAPITAL",    "20.0"))
SIM_TRADE_PCT       = float(os.getenv("SIM_TRADE_PCT",  "0.05"))   # 5% base por trade
SIM_MIN_TRADE       = float(os.getenv("SIM_MIN_TRADE",  "0.50"))   # mínimo $0.50 por trade
SIM_LIQUIDATION     = float(os.getenv("SIM_LIQUIDATION","2.0"))    # pausar si balance < $2

# Tokens que son "dinero" (SOL, USDC, USDT)
STABLE_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


# ── Persistencia ──────────────────────────────────────────────────────────────

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


def _load_balance() -> float:
    if os.path.exists(BALANCE_FILE):
        try:
            with open(BALANCE_FILE) as f:
                return float(json.load(f).get("balance", SIM_INITIAL_CAPITAL))
        except Exception:
            pass
    return SIM_INITIAL_CAPITAL


def _save_positions():
    with open(POSITIONS_FILE, "w") as f:
        json.dump(_positions, f, indent=2)


def _save_history():
    with open(HISTORY_FILE, "w") as f:
        json.dump(_history, f, indent=2)


def _save_balance():
    with open(BALANCE_FILE, "w") as f:
        json.dump({
            "balance":       round(_sim_balance, 4),
            "initial":       SIM_INITIAL_CAPITAL,
            "updated_at":    datetime.now().strftime("%H:%M:%S %d/%m/%Y"),
        }, f, indent=2)


# ── Estado en memoria ─────────────────────────────────────────────────────────

_positions:   dict[str, dict[str, dict]] = _load_positions()
_history:     list[dict]                 = _load_history()
_sim_balance: float                      = _load_balance()


# ── Capital dinámico ──────────────────────────────────────────────────────────

def _get_trade_pct() -> float:
    """
    Devuelve el % del balance a usar en el próximo trade.
    Usa los mismos SCALING_TIERS que el executor real:
    más ganancia acumulada → techo de trade más alto.
    """
    if SIM_INITIAL_CAPITAL <= 0:
        return SIM_TRADE_PCT
    profit_pct = (_sim_balance - SIM_INITIAL_CAPITAL) / SIM_INITIAL_CAPITAL
    for min_profit, trade_pct in reversed(SCALING_TIERS):
        if profit_pct >= min_profit:
            return trade_pct
    return SIM_TRADE_PCT


def _get_trade_amount() -> float:
    """Calcula el monto en USD para el próximo trade."""
    return _sim_balance * _get_trade_pct()


def _get_price(mint: str) -> float | None:
    pair = get_best_pair(mint)
    if not pair:
        return None
    try:
        return float(pair.get("priceUsd") or 0) or None
    except (ValueError, TypeError):
        return None


# ── Entrada pública ───────────────────────────────────────────────────────────

def process(swap: dict):
    """
    Analiza un swap y actualiza posiciones simuladas.
    Llamar desde executor.execute_copy() en cada swap detectado.
    """
    global _sim_balance

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


# ── Compra ────────────────────────────────────────────────────────────────────

def _handle_buy(wallet: str, label: str, token_mint: str, symbol: str):
    """Abre posición simulada al precio actual usando % del balance."""
    global _sim_balance

    # Ya tenemos posición en este token para esta wallet
    if _positions.get(wallet, {}).get(token_mint):
        return

    # Balance demasiado bajo — simular liquidación
    if _sim_balance < SIM_LIQUIDATION:
        log.warning(
            f"[SIM] ⚠ Balance simulado [red]${_sim_balance:.2f}[/] < "
            f"mínimo ${SIM_LIQUIDATION} — trade cancelado"
        )
        return

    trade_amount = _get_trade_amount()
    if trade_amount < SIM_MIN_TRADE:
        log.debug(f"[SIM] Trade amount ${trade_amount:.2f} < mínimo ${SIM_MIN_TRADE} — ignorando")
        return

    price = _get_price(token_mint)
    if not price:
        log.debug(f"[SIM] No hay precio para {symbol} — no se abre posición")
        return

    entry_context = get_context(token_mint)
    tier_pct      = _get_trade_pct()

    pos = {
        "token_mint":    token_mint,
        "symbol":        symbol,
        "entry_price":   price,
        "amount_usd":    round(trade_amount, 4),
        "opened_at":     time.time(),
        "opened_str":    datetime.now().strftime("%H:%M:%S %d/%m"),
        "wallet":        wallet,
        "wallet_label":  label,
        "entry_context": entry_context,
    }

    _positions.setdefault(wallet, {})[token_mint] = pos
    _save_positions()

    ctx_str = ""
    if entry_context:
        mcap = entry_context.get("mcap_usd", 0)
        bp   = entry_context.get("buy_pressure", 0)
        ch1h = entry_context.get("change_1h_pct", 0)
        ctx_str = (
            f" | mcap [white]${mcap:,.0f}[/]"
            f" | bp [white]{bp:.0%}[/]"
            f" | 1h [white]{ch1h:+.1f}%[/]"
        )

    profit_pct = (_sim_balance - SIM_INITIAL_CAPITAL) / SIM_INITIAL_CAPITAL * 100
    log.info(
        f"[SIM] 📥 ENTRADA | [cyan]{label}[/] compró [yellow]{symbol}[/] | "
        f"precio: [white]${price:.8f}[/] | "
        f"trade: [green]${trade_amount:.2f}[/] ({tier_pct*100:.0f}% de ${_sim_balance:.2f})"
        f"{ctx_str}"
    )


# ── Venta ─────────────────────────────────────────────────────────────────────

def _handle_sell(wallet: str, label: str, token_mint: str, symbol: str):
    """Cierra posición simulada, actualiza balance y calcula P&L."""
    global _sim_balance

    pos = _positions.get(wallet, {}).pop(token_mint, None)
    if not pos:
        log.debug(f"[SIM] Venta de {symbol} sin posición abierta — ignorando")
        return

    price_exit = _get_price(token_mint)
    if not price_exit:
        log.debug(f"[SIM] No hay precio de salida para {symbol} — no se cierra")
        _positions.setdefault(wallet, {})[token_mint] = pos
        return

    entry      = pos["entry_price"]
    amount_usd = pos["amount_usd"]
    pnl_pct    = (price_exit - entry) / entry * 100
    pnl_usd    = amount_usd * pnl_pct / 100
    hold_min   = (time.time() - pos["opened_at"]) / 60
    won        = pnl_pct > 0

    # Actualizar balance compuesto
    balance_before = _sim_balance
    _sim_balance   = max(0.0, _sim_balance + pnl_usd)
    _save_balance()

    exit_context  = get_context(token_mint)
    entry_context = pos.get("entry_context", {})

    trade = {
        "symbol":          symbol,
        "token":           token_mint,
        "wallet":          wallet,
        "wallet_label":    label,
        "entry_price":     entry,
        "exit_price":      price_exit,
        "pnl_pct":         round(pnl_pct,       2),
        "pnl_usd":         round(pnl_usd,        2),
        "amount_usd":      round(amount_usd,     4),
        "hold_min":        round(hold_min,        1),
        "won":             won,
        "balance_before":  round(balance_before, 4),
        "balance_after":   round(_sim_balance,   4),
        "opened_str":      pos["opened_str"],
        "closed_str":      datetime.now().strftime("%H:%M:%S %d/%m"),
        "timestamp":       time.time(),
        "entry_context":   entry_context,
        "exit_context":    exit_context,
    }
    _history.append(trade)
    _save_positions()
    _save_history()

    # Actualizar reglas del learner
    try:
        from copytrade import learner
        learner.update()
    except Exception as e:
        log.debug(f"[LEARNER] Error actualizando reglas: {e}")

    icon  = "[bold green]✅ WIN [/]" if won else "[bold red]❌ LOSS[/]"
    color = "green" if won else "red"
    bal_color = "green" if _sim_balance >= SIM_INITIAL_CAPITAL else "red"

    log.info(
        f"[SIM] {icon} | [cyan]{label}[/] vendió [yellow]{symbol}[/] | "
        f"[{color}]{pnl_pct:+.1f}%[/] ([{color}]${pnl_usd:+.2f}[/]) | "
        f"entrada ${entry:.8f} → salida ${price_exit:.8f} | "
        f"{hold_min:.0f} min | "
        f"balance: [{bal_color}]${_sim_balance:.2f}[/]"
    )

    _print_summary()


# ── Resumen ───────────────────────────────────────────────────────────────────

def _print_summary():
    if not _history:
        return

    wins      = [t for t in _history if t["won"]]
    losses    = [t for t in _history if not t["won"]]
    pnl_total = _sim_balance - SIM_INITIAL_CAPITAL
    win_rate  = len(wins) / len(_history) * 100
    roi_pct   = pnl_total / SIM_INITIAL_CAPITAL * 100

    log.info(
        f"[SIM] 📊 RESUMEN | "
        f"Trades: [white]{len(_history)}[/] | "
        f"Win rate: [{'green' if win_rate >= 50 else 'red'}]{win_rate:.0f}%[/] "
        f"([green]{len(wins)}W[/]/[red]{len(losses)}L[/]) | "
        f"Balance: [{'green' if _sim_balance >= SIM_INITIAL_CAPITAL else 'red'}]"
        f"${_sim_balance:.2f}[/] | "
        f"ROI: [{'green' if roi_pct >= 0 else 'red'}]{roi_pct:+.1f}%[/]"
    )


def get_summary() -> dict:
    wins      = [t for t in _history if t["won"]]
    pnl_total = _sim_balance - SIM_INITIAL_CAPITAL
    open_pos  = sum(len(v) for v in _positions.values())
    return {
        "total_trades":    len(_history),
        "wins":            len(wins),
        "losses":          len(_history) - len(wins),
        "win_rate":        len(wins) / len(_history) * 100 if _history else 0,
        "balance":         round(_sim_balance, 2),
        "initial_capital": SIM_INITIAL_CAPITAL,
        "pnl_total_usd":   round(pnl_total, 2),
        "roi_pct":         round(pnl_total / SIM_INITIAL_CAPITAL * 100, 1) if SIM_INITIAL_CAPITAL else 0,
        "open_positions":  open_pos,
        "history":         _history[-20:],
    }

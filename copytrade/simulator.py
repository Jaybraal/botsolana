"""
Simulador de P&L realista — replica exactamente lo que pasaría en live.

Reglas de realismo:
- Capital inicial = SIM_CAPITAL (valor real en USD de tu wallet)
- Trade size = mismo % que en live: MAX_TRADE_PCT + SCALING_TIERS de config.py
- Slippage = SIM_SLIPPAGE_PCT por leg (compra + venta) — defecto 8% (Pump.fun BC real)
- Fee = SIM_PRIORITY_FEE_SOL * precio_sol (round-trip real)
- El balance compone igual que en live: a mayor ganancia, mayor % por trade
"""

import json
import os
import time
import threading
from datetime import datetime

from config import MAX_TRADE_PCT, SCALING_TIERS
from utils.dexscreener import get_best_pair
from utils.market_context import get_context
from utils.logger import get_logger

# Caché del precio de SOL en USD — se refresca cada 60s
_sol_price_usd:       float = 0.0
_sol_price_fetched_at: float = 0.0

def _get_sol_price_usd() -> float:
    global _sol_price_usd, _sol_price_fetched_at
    if time.time() - _sol_price_fetched_at < 60 and _sol_price_usd > 0:
        return _sol_price_usd
    try:
        pair = get_best_pair("So11111111111111111111111111111111111111112")
        if pair:
            _sol_price_usd = float(pair.get("priceUsd") or 0)
            _sol_price_fetched_at = time.time()
    except Exception:
        pass
    return _sol_price_usd if _sol_price_usd > 0 else 150.0

log = get_logger("simulator")

os.makedirs("data", exist_ok=True)
POSITIONS_FILE = "data/sim_positions.json"
HISTORY_FILE   = "data/sim_history.json"
BALANCE_FILE   = "data/sim_balance.json"

# Si SIM_RESET=true, borra datos previos y empieza desde SIM_CAPITAL limpio.
# Poner en false después del primer deploy para que no resetee en reinicios.
if os.getenv("SIM_RESET", "false").lower() == "true":
    for _f in [POSITIONS_FILE, HISTORY_FILE, BALANCE_FILE]:
        if os.path.exists(_f):
            os.remove(_f)

# Capital inicial configurado en .env (default $45)
SIM_INITIAL_CAPITAL  = float(os.getenv("SIM_CAPITAL",         "22.0"))
SIM_MIN_TRADE        = float(os.getenv("SIM_MIN_TRADE",        "0.50"))   # mínimo $0.50 por trade
SIM_LIQUIDATION      = float(os.getenv("SIM_LIQUIDATION",      "2.0"))    # pausar si balance < $2
SIM_PRIORITY_FEE_SOL = float(os.getenv("SIM_PRIORITY_FEE_SOL", "0.0004")) # 0.0002 SOL × 2 round-trip
SIM_SLIPPAGE_PCT     = float(os.getenv("SIM_SLIPPAGE_PCT",      "0.08"))  # 8% por leg (Pump.fun BC)

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

_positions:   dict[str, dict] = _load_positions()  # {token_mint: position}
_history:     list[dict]                 = _load_history()
_sim_balance: float                      = _load_balance()
_lock = threading.Lock()  # protege _positions contra race conditions


# ── Capital dinámico ──────────────────────────────────────────────────────────

def _get_trade_pct() -> float:
    """Mismo escalado que executor._get_dynamic_trade_pct, expresado en USD."""
    if SIM_INITIAL_CAPITAL <= 0:
        return MAX_TRADE_PCT
    profit_pct = (_sim_balance - SIM_INITIAL_CAPITAL) / SIM_INITIAL_CAPITAL
    for min_profit, trade_pct in reversed(SCALING_TIERS):
        if profit_pct >= min_profit:
            return trade_pct
    return MAX_TRADE_PCT


def _get_trade_amount() -> float:
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

    wallet_buy_time = swap.get("wallet_buy_time")

    # Precio implícito: usar el precalculado de PumpPortal (ya en UI) o calcularlo desde Helius
    implied_price: float = 0.0
    if is_buy:
        if swap.get("implied_price_sol", 0) > 0:
            # PumpPortal: precio en SOL/token ya correcto (tokenAmount en UI)
            implied_price = swap["implied_price_sol"] * _get_sol_price_usd()
        elif swap.get("amount_in", 0) > 0 and swap.get("amount_out", 0) > 0:
            # Helius: amount_out en unidades mínimas → dividir por 1e6
            sol_amount   = swap["amount_in"] / 1_000_000_000
            token_amount = swap["amount_out"] / 1_000_000
            if token_amount > 0:
                implied_price = (sol_amount / token_amount) * _get_sol_price_usd()

    if is_buy:
        _handle_buy(wallet, wallet_label, token_out, symbol_out, wallet_buy_time, implied_price)
    elif is_sell:
        _handle_sell(wallet, wallet_label, token_in, symbol_in)


# ── Compra ────────────────────────────────────────────────────────────────────

def _handle_buy(wallet: str, label: str, token_mint: str, symbol: str,
                wallet_buy_time: float | None = None, implied_price: float = 0.0):
    """Abre posición simulada al precio actual usando % del balance."""
    global _sim_balance

    with _lock:
        # Ya tenemos posición en este token — igual que el executor real
        if _positions.get(token_mint):
            return
        # Reservar el slot inmediatamente para evitar race conditions
        _positions[token_mint] = {}  # placeholder

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

    detected_at = time.time()
    opened_at = wallet_buy_time if wallet_buy_time else detected_at
    latency_s = detected_at - opened_at if wallet_buy_time else 0

    # Precio: DexScreener primero, precio implícito del swap como fallback
    price = _get_price(token_mint) or implied_price
    if not price:
        log.debug(f"[SIM] No hay precio para {symbol} — no se abre posición")
        return

    entry_context = get_context(token_mint)
    tier_pct      = _get_trade_pct()

    pos = {
        "token_mint":      token_mint,
        "symbol":          symbol,
        "entry_price":     price,
        "amount_usd":      round(trade_amount, 4),
        "opened_at":       opened_at,
        "opened_str":      datetime.now().strftime("%H:%M:%S %d/%m"),
        "wallet":          wallet,
        "wallet_label":    label,
        "entry_context":   entry_context,
        "detection_delay": round(latency_s, 1),
    }

    _positions[token_mint] = pos
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

    latency_str = f" | delay [white]{latency_s:.0f}s[/]" if latency_s > 0.5 else ""
    slippage_cost = trade_amount * SIM_SLIPPAGE_PCT
    log.info(
        f"[SIM] 📥 ENTRADA | [cyan]{label}[/] compró [yellow]{symbol}[/] | "
        f"precio: [white]${price:.8f}[/] | "
        f"trade: [green]${trade_amount:.2f}[/] ({tier_pct*100:.0f}% de ${_sim_balance:.2f} balance) | "
        f"slip_entrada: [dim]-${slippage_cost:.3f} ({SIM_SLIPPAGE_PCT*100:.0f}%)[/]"
        f"{ctx_str}{latency_str}"
    )


# ── Venta ─────────────────────────────────────────────────────────────────────

def _handle_sell(wallet: str, label: str, token_mint: str, symbol: str):
    """Cierra posición simulada, actualiza balance y calcula P&L."""
    global _sim_balance

    with _lock:
        pos = _positions.pop(token_mint, None)
    if not pos or not pos.get("entry_price"):  # ignorar placeholders vacíos
        log.debug(f"[SIM] Venta de {symbol} sin posición abierta — ignorando")
        return

    hold_sec = time.time() - pos["opened_at"]
    hold_min = hold_sec / 60

    price_exit = _get_price(token_mint)
    if not price_exit:
        log.debug(f"[SIM] No hay precio de salida para {symbol} — no se cierra")
        _positions[token_mint] = pos
        return

    entry      = pos["entry_price"]
    amount_usd = pos["amount_usd"]

    # Ajuste de slippage: compramos peor y vendemos peor que el precio de mercado
    entry_adj     = entry * (1 + SIM_SLIPPAGE_PCT)
    exit_adj      = price_exit * (1 - SIM_SLIPPAGE_PCT)
    pnl_pct       = (exit_adj - entry_adj) / entry_adj * 100
    pnl_usd       = amount_usd * pnl_pct / 100

    # Fees reales: priority fee round-trip en USD
    fee_usd  = SIM_PRIORITY_FEE_SOL * _get_sol_price_usd()
    pnl_usd -= fee_usd

    won = pnl_usd > 0

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
        "pnl_pct":         round(pnl_pct,    2),
        "pnl_usd":         round(pnl_usd,    2),
        "amount_usd":      round(amount_usd, 4),
        "hold_min":        round(hold_min,   1),
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

    total_cost_pct = (SIM_SLIPPAGE_PCT * 2 * 100) + (fee_usd / amount_usd * 100)
    log.info(
        f"[SIM] {icon} | [cyan]{label}[/] vendió [yellow]{symbol}[/] | "
        f"[{color}]{pnl_pct:+.1f}%[/] ([{color}]${pnl_usd:+.2f}[/]) | "
        f"entrada ${entry:.8f} → salida ${price_exit:.8f} | "
        f"coste real: [dim]slip {SIM_SLIPPAGE_PCT*200:.0f}% + fee ${fee_usd:.3f} = {total_cost_pct:.1f}%[/] | "
        f"{hold_min:.0f} min | balance: [{bal_color}]${_sim_balance:.2f}[/]"
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
    open_pos  = len(_positions)
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

"""
Gestión de posiciones abiertas y historial de trades.
  data/positions.json  — posiciones abiertas
  data/trades.json     — historial completo con peak/trough para análisis
"""

import json
import os
import time
from datetime import datetime

from config import SNIPER_STOP_PCT, SNIPER_MAX_HOLD_MIN, SNIPER_TRAIL_START, SNIPER_TRAIL_DIST
from utils.logger import get_logger

log = get_logger("positions")

os.makedirs("data", exist_ok=True)
POSITIONS_FILE = "data/positions.json"
TRADES_FILE    = "data/trades.json"


# ── Persistencia ────────────────────────────────────────────────────────

def _load_pos() -> dict:
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_pos(data: dict):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _load_trades() -> list:
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _append_trade(trade: dict):
    trades = _load_trades()
    trades.append(trade)
    with open(TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2)


# ── API pública ─────────────────────────────────────────────────────────

def open_position(
    token_address: str,
    pair_address:  str,
    symbol:        str,
    entry_price:   float,
    amount_usd:    float,
    mcap:          float = 0,
    age_hours:     float = 0,
    dex:           str   = "?",
    buys_5m:       int   = 0,
    change_5m:     float = 0,
    change_1h:     float = 0,
    change_6h:     float = 0,
    token_type:    str   = "runner",
):
    positions = _load_pos()
    positions[token_address] = {
        "token_address": token_address,
        "pair_address":  pair_address,
        "symbol":        symbol,
        "entry_price":   entry_price,
        "amount_usd":    amount_usd,
        "mcap_entry":    mcap,
        "age_hours":     age_hours,
        "dex":           dex,
        "buys_5m":       buys_5m,
        "change_5m":     change_5m,
        "change_1h":     change_1h,
        "change_6h":     change_6h,
        "token_type":    token_type,
        "opened_at":     time.time(),
        "opened_str":    datetime.now().strftime("%H:%M:%S %d/%m"),
    }
    _save_pos(positions)
    type_label = "[bold yellow]⚡PUMP[/]" if token_type == "pump" else "[bold blue]🏃RUNNER[/]"
    log.info(
        f"[bold green]▶ ENTRADA SIM[/] [cyan]{symbol}[/] {type_label} | "
        f"${entry_price:.8f} | MCap [yellow]${mcap:,.0f}[/] | "
        f"Edad [white]{age_hours:.1f}h[/] | DEX [white]{dex}[/] | "
        f"Buys5m [green]{buys_5m}[/] | 5m [{'green' if change_5m>=0 else 'red'}]{change_5m:+.1f}%[/]"
    )


def close_position(token_address: str, reason: str, exit_price: float):
    from sniper import price_cache  # importación local para evitar circular
    positions = _load_pos()
    pos = positions.pop(token_address, None)
    if not pos:
        return
    _save_pos(positions)

    entry      = pos["entry_price"]
    amount_usd = pos["amount_usd"]
    pnl_pct    = ((exit_price - entry) / entry * 100) if entry else 0
    pnl_usd    = amount_usd * pnl_pct / 100
    hold_min   = (time.time() - pos["opened_at"]) / 60
    symbol     = pos["symbol"]
    won        = pnl_pct > 0

    # Datos del price_cache (peak/trough durante la vida del trade)
    snap = price_cache.snapshot(token_address)
    price_cache.evict(token_address)

    trade = {
        # Identificación
        "symbol":       symbol,
        "token":        token_address,
        "pair":         pos["pair_address"],
        "dex":          pos.get("dex", "?"),
        # Precios
        "entry_price":  entry,
        "exit_price":   exit_price,
        # Rendimiento
        "pnl_pct":      round(pnl_pct,   2),
        "pnl_usd":      round(pnl_usd,   2),
        "peak_pct":     round(snap.get("peak_pct",   pnl_pct), 2),   # máximo alcanzado
        "trough_pct":   round(snap.get("trough_pct", pnl_pct), 2),   # mínimo alcanzado
        "n_samples":    snap.get("n_samples", 0),
        # Capital
        "amount_usd":   amount_usd,
        # Timing
        "hold_min":     round(hold_min, 1),
        "reason":       reason,
        "won":          won,
        # Contexto de entrada
        "mcap_entry":   pos.get("mcap_entry",  0),
        "age_hours":    pos.get("age_hours",   0),
        "buys_5m":      pos.get("buys_5m",     0),
        "change_5m":    pos.get("change_5m",   0),
        "change_1h":    pos.get("change_1h",   0),
        # Timestamps
        "opened_str":   pos["opened_str"],
        "closed_str":   datetime.now().strftime("%H:%M:%S %d/%m"),
        "timestamp":    time.time(),
        "hour_of_day":  datetime.now().hour,
    }
    _append_trade(trade)

    icon = "[bold green]✅ WIN [/]" if won else "[bold red]❌ LOSS[/]"
    peak_note = (
        f" [dim](pico [green]+{snap['peak_pct']:.1f}%[/])[/]"
        if snap.get("peak_pct", 0) > pnl_pct + 2
        else ""
    )
    log.info(
        f"{icon} [cyan]{symbol}[/] | "
        f"[{'green' if won else 'red'}]{pnl_pct:+.1f}%[/] "
        f"([{'green' if won else 'red'}]${pnl_usd:+.2f}[/])"
        f"{peak_note} | {hold_min:.0f}min | {reason}"
    )


def get_all() -> dict:
    return _load_pos()


def count() -> int:
    return len(_load_pos())


def is_open(token_address: str) -> bool:
    return token_address in _load_pos()


# ── Monitor de salidas usando el price_cache ──────────────────────────────

class ExitSignal:
    def __init__(self, token_address: str, reason: str, current_price: float):
        self.token_address = token_address
        self.reason        = reason
        self.current_price = current_price


def check_exits() -> list[ExitSignal]:
    """
    Decide salidas combinando trailing stop dinámico + lectura de momentum en vivo.

    Regla principal: el trail es el gatillo, pero el momentum es el árbitro.
      - Si el trail se toca Y el momentum sigue fuerte (5m subiendo + mayoría compradores)
        → NO cerrar, extender el trail y dejar correr.
      - Si el trail se toca Y el momentum está muriendo → cerrar.

    Tipos de token:
      - pump  : también cierra por emergencia si 5m cae fuerte + más vendedores
      - runner: aguanta más las correcciones, solo cierra cuando el trail falla
    """
    from sniper import price_cache
    from config import (
        SNIPER_STOP_PCT, SNIPER_MAX_HOLD_MIN, SNIPER_TRAIL_START,
        SNIPER_HOLD_BUY_RATIO, SNIPER_HOLD_5M_MIN,
        SNIPER_PUMP_EXIT_5M, SNIPER_PUMP_EXIT_BUY_RATIO,
        SNIPER_TRAIL_DIST, SNIPER_TRAIL_DIST_PUMP,
    )

    signals: list[ExitSignal] = []

    for token_address, pos in _load_pos().items():
        c = price_cache.get(token_address)
        if not c:
            continue

        current        = c["price"]
        pnl_pct        = c["pnl_pct"]
        trail_sl       = c["trail_sl"]
        trail_active   = c["trail_active"]
        peak           = c["peak_pct"]
        hold_min       = (time.time() - pos["opened_at"]) / 60
        symbol         = pos["symbol"]
        token_type     = pos.get("token_type", "runner")
        ch_5m          = c.get("ch_5m_live",     0.0)
        buy_ratio      = c.get("buy_ratio_live",  0.5)
        trail_dist     = SNIPER_TRAIL_DIST_PUMP if token_type == "pump" else SNIPER_TRAIL_DIST

        # ── Salida de emergencia (solo pumps) ──────────────────────────
        # Si el precio cae fuerte en 5m Y hay más vendedores que compradores → giro brusco
        if (token_type == "pump"
                and ch_5m <= SNIPER_PUMP_EXIT_5M
                and buy_ratio <= SNIPER_PUMP_EXIT_BUY_RATIO):
            signals.append(ExitSignal(
                token_address,
                f"emergencia pump: 5m {ch_5m:+.1f}% | buys {buy_ratio:.0%}",
                current,
            ))
            continue

        if trail_active:
            # ── Trail activo: ¿cerramos o aguantamos? ──────────────────
            if pnl_pct <= trail_sl:
                momentum_vivo = (
                    ch_5m >= SNIPER_HOLD_5M_MIN
                    and buy_ratio >= SNIPER_HOLD_BUY_RATIO
                )
                if momentum_vivo:
                    # Precio sigue subiendo con fuerza → extender trail y aguantar
                    log.info(
                        f"  [bold magenta]⏳ HOLD[/] [cyan]{symbol}[/] | "
                        f"trail tocado pero momentum vivo | "
                        f"{pnl_pct:+.1f}% | pico +{peak:.1f}% | "
                        f"5m [green]{ch_5m:+.1f}%[/] | buys [green]{buy_ratio:.0%}[/]"
                    )
                    # Extendemos el trail: dejamos 5% más de margen desde el pico actual
                    price_cache._cache[token_address]["trail_sl"] = peak - trail_dist - 5
                else:
                    signals.append(ExitSignal(
                        token_address,
                        f"trail SL (pico +{peak:.0f}% → salida {pnl_pct:+.1f}%)",
                        current,
                    ))
            else:
                log.debug(
                    f"  {symbol}: {pnl_pct:+.1f}% | "
                    f"pico +{peak:.1f}% | trail SL {trail_sl:+.1f}% | "
                    f"5m {ch_5m:+.1f}% | buys {buy_ratio:.0%} | {hold_min:.0f}min"
                )
        else:
            # ── Fase inicial: SL fijo ───────────────────────────────────
            if pnl_pct <= -SNIPER_STOP_PCT:
                signals.append(ExitSignal(
                    token_address,
                    f"SL inicial {pnl_pct:+.1f}%",
                    current,
                ))
            elif hold_min >= SNIPER_MAX_HOLD_MIN:
                signals.append(ExitSignal(
                    token_address,
                    f"timeout {hold_min:.0f}min (sin activar trail)",
                    current,
                ))
            else:
                log.debug(
                    f"  {symbol}: {pnl_pct:+.1f}% | "
                    f"esperando trail | 5m {ch_5m:+.1f}% | buys {buy_ratio:.0%} | {hold_min:.0f}min"
                )

    return signals

"""
Cache de precios en vivo para posiciones abiertas.
Calcula trailing stop dinámico por posición según el tipo de token.

Tipos de token:
  - "pump"  : explosión reciente (1h domina sobre 6h) → trail activo desde +12%, dist 8%
  - "runner": tendencia sostenida multi-hora          → trail activo desde +20%, dist 15%

Trailing stop logic:
  - Fase 1: SL fijo en -STOP_PCT hasta alcanzar el umbral de activación del tipo
  - Fase 2: SL = peak_pct - trail_dist (sigue el precio arriba, nunca baja)
  - Sin TP fijo — aguanta mientras el precio siga subiendo

Momentum check (integrado en el cache):
  - buy_ratio_live: proporción buys/(buys+sells) última hora
  - ch_5m_live    : cambio de precio últimos 5 minutos
  Estos datos alimentan la lógica de "aguantar aunque el trail se toque".
"""

import time
from utils.dexscreener import get_pair_full
from utils.logger import get_logger

log = get_logger("price_cache")

_cache: dict[str, dict] = {}


def _trail_params(token_type: str) -> tuple[float, float]:
    """Devuelve (trail_start, trail_dist) según el tipo de token."""
    from config import (
        SNIPER_TRAIL_START, SNIPER_TRAIL_DIST,
        SNIPER_TRAIL_START_PUMP, SNIPER_TRAIL_DIST_PUMP,
    )
    if token_type == "pump":
        return SNIPER_TRAIL_START_PUMP, SNIPER_TRAIL_DIST_PUMP
    return SNIPER_TRAIL_START, SNIPER_TRAIL_DIST


def update_all(positions: dict) -> dict[str, dict]:
    """
    Actualiza precios, momentum en vivo y trailing stop para cada posición.
    Usa get_pair_full() para obtener priceChange.m5 y txns.h1 en cada poll.
    """
    from config import SNIPER_STOP_PCT

    for token_addr, pos in positions.items():
        pair_addr  = pos["pair_address"]
        entry      = pos["entry_price"]
        token_type = pos.get("token_type", "runner")

        pair = get_pair_full(pair_addr)
        if pair is None:
            continue

        try:
            current = float(pair.get("priceUsd") or 0)
        except (ValueError, TypeError):
            continue
        if current <= 0 or entry <= 0:
            continue

        pnl_pct = (current - entry) / entry * 100

        # Momentum en vivo
        ch_5m_live = float((pair.get("priceChange") or {}).get("m5", 0) or 0)
        t1h        = (pair.get("txns") or {}).get("h1", {})
        buys_1h    = int(t1h.get("buys",  0))
        sells_1h   = int(t1h.get("sells", 0))
        total_1h   = buys_1h + sells_1h
        buy_ratio_live = buys_1h / total_1h if total_1h > 0 else 0.5

        prev    = _cache.get(token_addr, {})
        peak    = max(prev.get("peak_pct",   pnl_pct), pnl_pct)
        trough  = min(prev.get("trough_pct", pnl_pct), pnl_pct)
        history = prev.get("history", [])

        # ── Trailing stop dinámico según tipo de token ──────────────────
        trail_start, trail_dist = _trail_params(token_type)

        if peak >= trail_start:
            raw_trail  = peak - trail_dist
            prev_trail = prev.get("trail_sl", -SNIPER_STOP_PCT)
            trail_sl   = max(raw_trail, prev_trail)  # el SL solo sube, nunca baja
            trail_active = True
        else:
            trail_sl     = -SNIPER_STOP_PCT
            trail_active = False

        history.append({"t": time.time(), "price": current, "pnl_pct": round(pnl_pct, 3)})
        if len(history) > 500:
            history = history[-500:]

        _cache[token_addr] = {
            "price":          current,
            "pnl_pct":        pnl_pct,
            "peak_pct":       peak,
            "trough_pct":     trough,
            "trail_sl":       trail_sl,
            "trail_active":   trail_active,
            "token_type":     token_type,
            "ch_5m_live":     ch_5m_live,
            "buy_ratio_live": buy_ratio_live,
            "history":        history,
            "updated_at":     time.time(),
        }

    return _cache


def get(token_address: str) -> dict | None:
    return _cache.get(token_address)


def get_price(token_address: str) -> float | None:
    c = _cache.get(token_address)
    return c["price"] if c else None


def get_pnl(token_address: str) -> float:
    c = _cache.get(token_address)
    return c["pnl_pct"] if c else 0.0


def get_peak(token_address: str) -> float:
    c = _cache.get(token_address)
    return c["peak_pct"] if c else 0.0


def get_trail_sl(token_address: str) -> float:
    from config import SNIPER_STOP_PCT
    c = _cache.get(token_address)
    return c["trail_sl"] if c else -SNIPER_STOP_PCT


def evict(token_address: str):
    _cache.pop(token_address, None)


def snapshot(token_address: str) -> dict:
    c = _cache.get(token_address, {})
    return {
        "peak_pct":     c.get("peak_pct",     0.0),
        "trough_pct":   c.get("trough_pct",   0.0),
        "trail_sl":     c.get("trail_sl",      0.0),
        "trail_active": c.get("trail_active", False),
        "n_samples":    len(c.get("history",   [])),
    }

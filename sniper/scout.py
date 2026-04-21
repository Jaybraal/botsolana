"""
Scout: encuentra tokens con momentum CONFIRMADO en múltiples timeframes.
Objetivo: detectar tokens como los que aparecen en DexScreener "Trending" —
  - Al menos 30 min de vida (no rugs recién salidos)
  - Alto volumen y transacciones (muchos traders reales)
  - Momentum positivo en 1h Y 6h (tendencia establecida, no spike puntual)
  - Más compradores que vendedores ahora mismo
"""

import time
from datetime import datetime, timezone

from config import (
    SNIPER_MIN_MCAP, SNIPER_MAX_MCAP,
    SNIPER_MIN_TOKEN_AGE, SNIPER_MAX_TOKEN_AGE,
    SNIPER_MIN_LIQ_USD, SNIPER_MIN_VOL_24H,
    SNIPER_MIN_TXNS_24H, SNIPER_MIN_TXNS_1H,
    SNIPER_MIN_CHANGE_5M,
    SNIPER_MIN_CHANGE_1H, SNIPER_MAX_CHANGE_1H, SNIPER_MIN_CHANGE_6H,
    SNIPER_MIN_BUY_RATIO,
    SNIPER_MAX_VOL_MCAP_RATIO, SNIPER_MIN_CHANGE_24H,
)
from utils.dexscreener import get_trending_solana, get_new_solana_tokens, get_tokens_batch
from utils.logger import get_logger

log = get_logger("scout")

_seen: set[str] = set()


def _classify(pair: dict) -> str:
    """
    Clasifica el token como "pump" o "runner".

    Pump  → la aceleración de la última hora es > 2.5x la tasa media de 6h.
            Sube rápido, puede caer igual de rápido. Trail corto y temprano.
    Runner→ tendencia sostenida a lo largo de horas.
            Aguanta mejor las correcciones. Trail amplio.
    """
    ch_1h = _change(pair, "h1")
    ch_6h = _change(pair, "h6")
    if ch_1h > 0 and ch_6h > 5:
        avg_rate_6h = ch_6h / 6          # % promedio por hora en las últimas 6h
        if ch_1h > avg_rate_6h * 2.5:   # hora actual > 2.5x el promedio → pump
            return "pump"
    return "runner"


def _age_hours(pair: dict) -> float:
    ms = pair.get("pairCreatedAt")
    if not ms:
        return 999.0
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    return (now_ms - ms) / 3_600_000


def _mcap(pair: dict) -> float:
    mc = pair.get("marketCap") or pair.get("fdv") or 0
    return float(mc)


def _txns_total(pair: dict, window: str) -> int:
    t = (pair.get("txns") or {}).get(window, {})
    return int(t.get("buys", 0)) + int(t.get("sells", 0))


def _buy_ratio(pair: dict, window: str) -> float:
    t = (pair.get("txns") or {}).get(window, {})
    buys  = int(t.get("buys",  0))
    sells = int(t.get("sells", 0))
    total = buys + sells
    return buys / total if total > 0 else 0.0


def _change(pair: dict, window: str) -> float:
    return float((pair.get("priceChange") or {}).get(window, 0) or 0)


def _vol(pair: dict, window: str) -> float:
    return float((pair.get("volume") or {}).get(window, 0) or 0)


def _score(pair: dict) -> float:
    """
    Score de momentum compuesto:
      - Peso alto al cambio 1h y 6h (momentum actual)
      - Peso al ratio buys/sells (presión compradora)
      - Peso al volumen (liquidez del movimiento)
      - Descuento por edad muy alta (token demasiado viejo pierde momentum)
    """
    ch_1h  = max(0, _change(pair, "h1"))
    ch_6h  = max(0, _change(pair, "h6"))
    ch_24h = max(0, _change(pair, "h24"))
    buy_r  = _buy_ratio(pair, "h1")
    vol_24 = _vol(pair, "h24")
    age_h  = _age_hours(pair)

    # Normalizar volumen (cap en $2M)
    vol_score = min(vol_24 / 2_000_000, 1.0) * 20

    # Penalizar tokens muy viejos (>12h pierden potencial)
    age_factor = max(0, 1 - (age_h - 6) / 18) if age_h > 6 else 1.0

    return (
        ch_1h  * 0.40 +
        ch_6h  * 0.25 +
        ch_24h * 0.10 +
        buy_r  * 15   +
        vol_score
    ) * age_factor


def _passes(pair: dict) -> tuple[bool, str]:
    age_h  = _age_hours(pair)
    mcap   = _mcap(pair)
    liq    = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
    vol24  = _vol(pair, "h24")
    ch_1h  = _change(pair, "h1")
    ch_6h  = _change(pair, "h6")
    price  = float(pair.get("priceUsd") or 0)

    txns_24h = _txns_total(pair, "h24")
    txns_1h  = _txns_total(pair, "h1")
    buy_r_1h = _buy_ratio(pair, "h1")

    if price <= 0:
        return False, "sin precio"
    if age_h < SNIPER_MIN_TOKEN_AGE:
        return False, f"muy nuevo ({age_h:.2f}h < {SNIPER_MIN_TOKEN_AGE}h)"
    if age_h > SNIPER_MAX_TOKEN_AGE:
        return False, f"muy viejo ({age_h:.1f}h)"
    if mcap < SNIPER_MIN_MCAP:
        return False, f"mcap bajo (${mcap:,.0f})"
    if mcap > SNIPER_MAX_MCAP:
        return False, f"mcap alto (${mcap:,.0f})"
    if liq < SNIPER_MIN_LIQ_USD:
        return False, f"liquidez baja (${liq:,.0f})"
    if vol24 < SNIPER_MIN_VOL_24H:
        return False, f"vol 24h bajo (${vol24:,.0f})"
    # ── Anti-rug ────────────────────────────────────────────────────────
    ch_24h = _change(pair, "h24")
    if ch_24h < SNIPER_MIN_CHANGE_24H:
        return False, f"dump 24h ({ch_24h:.0f}% < {SNIPER_MIN_CHANGE_24H}%) — posible rug previo"
    if mcap > 0:
        vol_mcap_ratio = vol24 / mcap
        if vol_mcap_ratio > SNIPER_MAX_VOL_MCAP_RATIO:
            return False, f"vol/mcap sospechoso ({vol_mcap_ratio:.0f}x > {SNIPER_MAX_VOL_MCAP_RATIO}x) — posible manipulación"
    if txns_24h < SNIPER_MIN_TXNS_24H:
        return False, f"pocas txns 24h ({txns_24h})"
    if txns_1h < SNIPER_MIN_TXNS_1H:
        return False, f"inactivo ahora ({txns_1h} txns 1h)"
    ch_5m  = _change(pair, "m5")
    if ch_5m < SNIPER_MIN_CHANGE_5M:
        return False, f"5m cayendo ({ch_5m:.1f}% < {SNIPER_MIN_CHANGE_5M}%) — no entrar ahora"
    if ch_1h < SNIPER_MIN_CHANGE_1H:
        return False, f"1h insuficiente ({ch_1h:.1f}%)"
    if ch_1h > SNIPER_MAX_CHANGE_1H:
        return False, f"1h extendido ({ch_1h:.1f}% > {SNIPER_MAX_CHANGE_1H}%) — probable techo"
    # 6h solo aplica si el token tiene más de 6h — tokens jóvenes no tienen historia 6h real
    if age_h > 6 and ch_6h < SNIPER_MIN_CHANGE_6H:
        return False, f"6h insuficiente ({ch_6h:.1f}%)"
    if buy_r_1h < SNIPER_MIN_BUY_RATIO:
        return False, f"más vendedores que compradores ({buy_r_1h:.0%})"

    return True, ""


def find_opportunities(exclude: set[str] | None = None) -> list[dict]:
    """
    Busca tokens con momentum confirmado en múltiples timeframes.
    Combina tokens boosteados + perfiles recientes de DexScreener.
    """
    skip = (exclude or set()) | _seen
    results: list[dict] = []
    checked = 0

    # Fuentes: trending (boosted) + perfiles recientes
    boosted  = get_trending_solana()
    profiles = get_new_solana_tokens()

    # Deduplicar
    seen_addrs: set[str] = set()
    token_list = []
    for t in boosted + profiles:
        addr = t.get("tokenAddress", "")
        if addr and addr not in seen_addrs:
            seen_addrs.add(addr)
            token_list.append(addr)

    # Filtrar skip antes del batch para no gastar requests en tokens ya vistos
    to_fetch = [addr for addr in token_list if addr not in skip]
    log.debug(f"Scout: consultando {len(to_fetch)} tokens en batch (era {len(token_list)} antes de skip)")

    pairs_by_token = get_tokens_batch(to_fetch)

    for addr in to_fetch:
        pairs = pairs_by_token.get(addr, [])
        if not pairs:
            continue

        checked += 1
        # Mejor par por liquidez
        pair = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
        ok, reason = _passes(pair)

        if not ok:
            log.debug(f"  ✗ {addr[:8]}...: {reason}")
            continue

        age_h   = _age_hours(pair)
        mcap    = _mcap(pair)
        liq     = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
        vol24   = _vol(pair, "h24")
        ch_5m   = _change(pair, "m5")
        ch_1h   = _change(pair, "h1")
        ch_6h   = _change(pair, "h6")
        ch_24h  = _change(pair, "h24")
        price   = float(pair.get("priceUsd") or 0)
        sym     = (pair.get("baseToken") or {}).get("symbol", addr[:6])
        dex     = pair.get("dexId", "?")
        txns_1h = _txns_total(pair, "h1")
        txns_24 = _txns_total(pair, "h24")
        buy_r   = _buy_ratio(pair, "h1")

        token_type = _classify(pair)
        opp = {
            "token_address": addr,
            "pair_address":  pair.get("pairAddress", ""),
            "symbol":        sym,
            "price_usd":     price,
            "mcap":          mcap,
            "liquidity_usd": liq,
            "age_hours":     round(age_h, 2),
            "vol_24h":       vol24,
            "txns_1h":       txns_1h,
            "txns_24h":      txns_24,
            "buy_ratio_1h":  round(buy_r, 2),
            "change_5m":     ch_5m,
            "change_1h":     ch_1h,
            "change_6h":     ch_6h,
            "change_24h":    ch_24h,
            "dex":           dex,
            "token_type":    token_type,
            "score":         round(_score(pair), 1),
        }
        results.append(opp)

        type_tag = (
            "[bold yellow]⚡PUMP[/]" if token_type == "pump"
            else "[bold blue]🏃RUNNER[/]"
        )
        log.info(
            f"  [bold green]✓[/] [cyan]{sym:8s}[/] {type_tag} | "
            f"MCap [yellow]${mcap:>8,.0f}[/] | "
            f"Edad [white]{age_h:.1f}h[/] | "
            f"Vol24 [white]${vol24:>8,.0f}[/] | "
            f"Txns1h [white]{txns_1h:,}[/] | "
            f"1h [{'green' if ch_1h>=0 else 'red'}]{ch_1h:+.0f}%[/] "
            f"6h [{'green' if ch_6h>=0 else 'red'}]{ch_6h:+.0f}%[/] "
            f"24h [{'green' if ch_24h>=0 else 'red'}]{ch_24h:+.0f}%[/] | "
            f"Buys [green]{buy_r:.0%}[/] | Score [bold]{opp['score']}[/]"
        )

    log.debug(f"Scout: {checked} revisados, {len(results)} calificados")

    # Ordenar por score
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def mark_seen(token_address: str):
    _seen.add(token_address)

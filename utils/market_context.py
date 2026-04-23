"""
Captura snapshot de mercado de un token via DexScreener en el momento del trade.
Provee mcap, liquidez, volumen, tendencia, buy pressure y edad del par.
"""
import time
from utils.dexscreener import get_best_pair
from utils.logger import get_logger

log = get_logger("market_context")


def get_context(token_mint: str) -> dict:
    """
    Retorna snapshot del mercado para un token en este momento.
    Retorna {} si no hay datos disponibles (token muy nuevo, sin par, etc.).
    """
    try:
        pair = get_best_pair(token_mint)
        if not pair:
            return {}

        price_usd = float(pair.get("priceUsd") or 0)
        mcap      = float(pair.get("marketCap") or pair.get("fdv") or 0)
        liq       = float((pair.get("liquidity") or {}).get("usd") or 0)
        vol_24h   = float((pair.get("volume")    or {}).get("h24") or 0)

        pc         = pair.get("priceChange") or {}
        change_5m  = float(pc.get("m5")  or 0)
        change_1h  = float(pc.get("h1")  or 0)
        change_6h  = float(pc.get("h6")  or 0)
        change_24h = float(pc.get("h24") or 0)

        txns_1h  = (pair.get("txns") or {}).get("h1") or {}
        buys_1h  = int(txns_1h.get("buys",  0))
        sells_1h = int(txns_1h.get("sells", 0))
        total_1h = buys_1h + sells_1h
        buy_pressure = buys_1h / total_1h if total_1h > 0 else 0.5

        created_at = pair.get("pairCreatedAt")  # timestamp en ms
        age_days = round((time.time() * 1000 - created_at) / (1000 * 86400), 2) if created_at else None

        return {
            "price_usd":      round(price_usd,              10),
            "mcap_usd":       round(mcap,                    2),
            "liquidity_usd":  round(liq,                     2),
            "volume_24h_usd": round(vol_24h,                 2),
            "vol_liq_ratio":  round(vol_24h / liq, 3) if liq > 0 else 0,
            "change_5m_pct":  round(change_5m,               2),
            "change_1h_pct":  round(change_1h,               2),
            "change_6h_pct":  round(change_6h,               2),
            "change_24h_pct": round(change_24h,              2),
            "buy_pressure":   round(buy_pressure,             3),
            "buys_1h":        buys_1h,
            "sells_1h":       sells_1h,
            "age_days":       age_days,
            "dex_id":         pair.get("dexId", "unknown"),
        }
    except Exception as e:
        log.debug(f"Error capturando contexto de {token_mint[:8]}...: {e}")
        return {}

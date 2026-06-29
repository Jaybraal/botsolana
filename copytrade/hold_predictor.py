"""
Predictor de tipo de hold basado en análisis empírico de sim_history.json.

Aprendido de 131 trades con entry_context (46W / 85L):
  - age_days:       wins avg 5.31d  vs losses 0.06d   (+8529%)
  - mcap_usd:       wins avg $25K   vs losses $64K    (-60%)
  - change_1h_pct:  wins avg 125%   vs losses 167%    (-25%)
  - vol_liq_ratio:  wins avg 6.29   vs losses 1.92    (+227%)

Predice si un trade será "momentum" (copiar) vs "snipe" (saltar).
Retorna (score 0-100, passed bool, reason str)
"""

import os

# Threshold propio — separado del SCORER_THRESHOLD de Groq (que usa 70)
# Backtesting sobre 132 trades: thr=35 → WR 41%, PnL +517 (+121% vs sin filtro)
THRESHOLD = int(os.getenv("HOLD_PREDICTOR_THRESHOLD", "35"))

# Wallets cuyas operaciones rápidas (<1min) son incopiables:
#   Theo: 36 trades <1min → 17% WR (solo gana cuando hold 1-5min → 80% WR)
SNIPE_WALLETS = {"Theo", "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt"}


def predict(wallet_label: str, entry_context: dict) -> tuple[int, bool, str]:
    """
    Evalúa si vale la pena copiar este trade basado en features del token.

    Retorna: (score 0-100, passed, reason)
    """
    ctx = entry_context or {}
    score = 50
    reasons: list[str] = []

    age_days    = ctx.get("age_days") or 0
    age_min     = age_days * 1440
    mcap        = ctx.get("mcap_usd") or 0
    change_1h   = ctx.get("change_1h_pct") or 0
    change_5m   = ctx.get("change_5m_pct") or 0
    buy_press   = ctx.get("buy_pressure") or 0.5
    liquidity   = ctx.get("liquidity_usd") or 0
    vol_liq     = ctx.get("vol_liq_ratio") or 0
    buys_1h     = ctx.get("buys_1h") or 0
    sells_1h    = ctx.get("sells_1h") or 0

    # ── Feature 1: Edad del token (+8529% diferencia — el más poderoso) ─────────
    if age_min < 5:
        score -= 30
        reasons.append(f"-30 token nuevo ({age_min:.0f}min)")
    elif age_min < 15:
        score -= 20
        reasons.append(f"-20 token reciente ({age_min:.0f}min)")
    elif age_min < 30:
        score -= 10
        reasons.append(f"-10 token joven ({age_min:.0f}min)")
    elif age_min < 60:
        score += 5
        reasons.append(f"+5 token maduro ({age_min:.0f}min)")
    elif age_days < 1:
        score += 15
        reasons.append(f"+15 token estable ({age_min:.0f}min)")
    else:
        score += 25
        reasons.append(f"+25 token veterano ({age_days:.1f}d)")

    # ── Feature 2: Market cap (-60% diferencia) ──────────────────────────────────
    if mcap > 0:
        if mcap < 10_000:
            score += 20
            reasons.append(f"+20 mcap micro (${mcap:,.0f})")
        elif mcap < 30_000:
            score += 10
            reasons.append(f"+10 mcap bajo (${mcap:,.0f})")
        elif mcap < 60_000:
            pass  # neutro
        else:
            score -= 20
            reasons.append(f"-20 mcap alto (${mcap:,.0f})")

    # ── Feature 3: Cambio precio 1h (-25% diferencia) ────────────────────────────
    if change_1h > 250:
        score -= 25
        reasons.append(f"-25 sobreextendido 1h (+{change_1h:.0f}%)")
    elif change_1h > 150:
        score -= 12
        reasons.append(f"-12 ya subió mucho 1h (+{change_1h:.0f}%)")
    elif change_1h > 100:
        score -= 5
    elif 20 <= change_1h <= 100:
        score += 10
        reasons.append(f"+10 momentum sano 1h (+{change_1h:.0f}%)")
    elif change_1h < 0:
        score -= 8
        reasons.append(f"-8 precio bajando 1h ({change_1h:.0f}%)")

    # ── Feature 4: Vol/Liq ratio (+227% diferencia) ──────────────────────────────
    if vol_liq > 10:
        score += 18
        reasons.append(f"+18 vol/liq alto ({vol_liq:.1f})")
    elif vol_liq > 6:
        score += 12
        reasons.append(f"+12 vol/liq bueno ({vol_liq:.1f})")
    elif vol_liq > 3:
        score += 5
    elif 0 < vol_liq < 2:
        score -= 10
        reasons.append(f"-10 vol/liq bajo ({vol_liq:.1f})")

    # ── Feature 5: Buy pressure ──────────────────────────────────────────────────
    if buy_press > 0.8:
        score -= 15
        reasons.append(f"-15 pánico comprador ({buy_press:.0%})")
    elif 0.52 <= buy_press <= 0.70:
        score += 8
        reasons.append(f"+8 buy pressure sano ({buy_press:.0%})")
    elif buy_press < 0.38:
        score -= 12
        reasons.append(f"-12 dominan vendedores ({buy_press:.0%})")

    # ── Feature 6: Actividad reciente (buys_1h vs sells_1h) ─────────────────────
    total_txs = buys_1h + sells_1h
    if total_txs > 0:
        real_bp = buys_1h / total_txs
        if real_bp > 0.55 and total_txs > 50:
            score += 5
            reasons.append(f"+5 flujo comprador ({buys_1h}B/{sells_1h}S)")
        elif real_bp < 0.40:
            score -= 8
            reasons.append(f"-8 flujo vendedor ({buys_1h}B/{sells_1h}S)")

    # ── Penalización por wallet con patrón snipe (Theo) ─────────────────────────
    # Theo gana 80% cuando hold 1-5min, pero 17% en <1min. Al copiar sus snipes
    # siempre llegamos tarde. Penalizamos tokens que lucen como snipe de Theo.
    wallet_clean = wallet_label.encode("ascii", "ignore").decode().strip()
    if wallet_clean in SNIPE_WALLETS:
        if age_min < 30 and change_1h > 80:
            score -= 25
            reasons.append(f"-25 perfil snipe-Theo (nuevo+ya subió)")
        elif age_min >= 30 or age_days >= 0.1:
            score += 10
            reasons.append(f"+10 Theo en modo momentum (hold probable 1-5min)")

    score = max(0, min(100, score))
    passed = score >= THRESHOLD
    reason = " | ".join(reasons) if reasons else "sin señales suficientes"

    return score, passed, reason

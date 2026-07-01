"""
Scorer estadístico — actualizado 29/06/2026 con 2,659 trades reales (WIN/LOSS).

Patrones medidos de wallets élite (Theo, Cupsey, Decu, Nyhrox, Cented, Domy):
  buys_5m 0-10      → WR 82.3%  (1,287 trades)
  buys_5m 10-100    → WR 90.1%  (81 trades)   ← zona óptima
  buys_5m 100-500   → WR 52.2%  (25 trades)   ← zona peligrosa
  buys_5m 500+      → WR 95.5%  (22 trades)   ← viral confirmado
  PumpSwap          → WR 90.0%  (532 trades)
  Pump.fun          → WR 77.8%  (2,105 trades)
  Raydium           → WR 100%   (19 trades — muestra pequeña)
  Jupiter           → WR 0.0%   (3 trades)    ← rechazar siempre
  token_age 0-5min  → WR 82.8%
  token_age 5-10min → WR 89.2%  ← mejor rango
  token_age 10-15m  → WR 87.0%
  token_age 15-30m  → WR 78.7%
  token_age 60min+  → WR 75.7%
  mcap 20k-50k      → WR 90.8%  ← mejor rango
  mcap <5k          → WR 80.8%
  mcap 100k+        → WR 84.0%
  price_1h -50-0%   → WR 86.3%  (dip comprable)
  price_1h 0-50%    → WR 79.9%
  price_1h 200%+    → WR 81.6%
"""

import os
from utils.logger import get_logger

log = get_logger("stat_scorer")

THRESHOLD = int(os.getenv("SCORER_THRESHOLD", "55"))


def score_token(token_info: dict) -> tuple[int, bool, str]:
    """
    Evalúa un token usando patrones estadísticos.
    Retorna (score 0-100, passed, reason_str).
    """
    score = 50   # base neutro
    reasons: list[str] = []

    age     = token_info.get("token_age_min")
    liq     = float(token_info.get("liquidity_usd") or 0)
    mcap    = float(token_info.get("mcap_usd") or 0)
    ch1h    = token_info.get("price_change_1h")
    ch5m    = token_info.get("price_change_5m")
    buys    = int(token_info.get("buys_5m") or 0)
    sells   = int(token_info.get("sells_5m") or 0)
    program = token_info.get("program", "")

    # ── Rechazos duros ───────────────────────────────────────────────────
    if program == "Jupiter":
        return (0, False, "Jupiter WR=0% — rechazado siempre")

    if 100 <= buys <= 500 and sells > 0:
        ratio = sells / buys if buys > 0 else 0
        if ratio > 0.7:
            return (0, False, f"buys 100-500 + ratio venta {ratio:.1f} — distribución activa [WR 52%]")

    # ── Programa ─────────────────────────────────────────────────────────
    if program == "PumpSwap":
        score += 20
        reasons.append("+20 PumpSwap [WR 90%]")
    elif program == "Raydium":
        score += 25
        reasons.append("+25 Raydium [WR 100%]")
    elif program == "Pump.fun":
        score += 5
        reasons.append("+5 Pump.fun [WR 78%]")

    # ── Buys 5m ──────────────────────────────────────────────────────────
    if buys >= 500:
        score += 20
        reasons.append(f"+20 buys_5m={buys} [WR 95% — viral]")
    elif 10 <= buys < 100:
        score += 15
        reasons.append(f"+15 buys_5m={buys} [WR 90% — zona óptima]")
    elif buys < 10:
        score += 5
        reasons.append(f"+5 buys_5m={buys} [WR 82%]")
    elif 100 <= buys < 500:
        score -= 15
        reasons.append(f"-15 buys_5m={buys} [WR 52% — zona peligrosa]")

    # ── Token age ────────────────────────────────────────────────────────
    if age is not None:
        if 5 <= age <= 10:
            score += 15
            reasons.append(f"+15 edad {age:.1f}min [WR 89% — óptimo]")
        elif age < 5:
            score += 10
            reasons.append(f"+10 edad {age:.1f}min [WR 83%]")
        elif 10 < age <= 30:
            score += 8
            reasons.append(f"+8 edad {age:.1f}min [WR 83%]")
        elif age > 30:
            score += 3
            reasons.append(f"+3 edad {age:.1f}min [WR 79%]")

    # ── MCap ─────────────────────────────────────────────────────────────
    if 20000 <= mcap <= 50000:
        score += 15
        reasons.append(f"+15 mcap ${mcap:.0f} [WR 90% — sweet spot]")
    elif 5000 <= mcap < 20000:
        score += 10
        reasons.append(f"+10 mcap ${mcap:.0f} [WR 87%]")
    elif 50000 <= mcap <= 100000:
        score += 8
        reasons.append(f"+8 mcap ${mcap:.0f} [WR 87%]")
    elif mcap > 100000:
        score += 5
        reasons.append(f"+5 mcap ${mcap:.0f} [WR 84%]")
    elif 0 < mcap < 5000:
        score += 3
        reasons.append(f"+3 mcap ${mcap:.0f} [WR 81%]")

    # ── Liquidez ─────────────────────────────────────────────────────────
    token_fresh = age is not None and age < 10
    if liq == 0:
        if not token_fresh:
            score -= 10
            reasons.append("-10 sin liquidez DexScreener (token >10min)")
    elif liq >= 1000:
        score += 10
        reasons.append(f"+10 liq ${liq:.0f} >= $1k")
    elif liq < 500:
        score -= 5
        reasons.append(f"-5 liq ${liq:.0f} < $500")

    # ── Price change 1h ──────────────────────────────────────────────────
    if ch1h is not None:
        if -50 <= ch1h < 0:
            score += 10
            reasons.append(f"+10 1h={ch1h:.0f}% [WR 86% — dip comprable]")
        elif 0 <= ch1h <= 50:
            score += 5
            reasons.append(f"+5 1h={ch1h:.0f}% [WR 80%]")
        elif ch1h > 200:
            score += 8
            reasons.append(f"+8 1h={ch1h:.0f}% [WR 82% — momentum fuerte]")
        elif ch1h < -50:
            score -= 5
            reasons.append(f"-5 1h={ch1h:.0f}% (caída fuerte)")

    # ── Price change 5m (momentum reciente) ──────────────────────────────
    if ch5m is not None:
        if ch5m > 20:
            score += 10
            reasons.append(f"+10 5m={ch5m:.0f}% — momentum activo")
        elif ch5m < -15:
            score -= 10
            reasons.append(f"-10 5m={ch5m:.0f}% — cayendo ahora")

    score = max(0, min(100, score))
    passed = score >= THRESHOLD
    reason = " | ".join(reasons) if reasons else "sin señales"

    return score, passed, reason


def should_buy(token_info: dict) -> tuple[bool, str]:
    """Interfaz simple: (comprar, motivo)."""
    score, passed, reason = score_token(token_info)
    log.info(f"[stat_scorer] score={score} {'✅ COMPRAR' if passed else '❌ SKIP'} | {reason}")
    return passed, f"score={score} | {reason}"

"""
Scorer estadístico derivado del análisis de 4,913 trades históricos en wallet_history.db.
No requiere Groq — determinista, siempre disponible.

Patrones medidos (WIN vs LOSS por rango):
  token_age 5-15min   → WR 60.1%
  token_age 15-30min  → WR 51.5%
  token_age 60min+    → WR 27.6%
  liq $2k-$10k        → WR 48.5%
  liq $0              → WR 37.9%
  mcap $5k-$20k       → WR 53.5%
  mcap $100k+         → WR 39.0%
  buys_5m 200+        → WR 68.1%
  buys_5m 50-200      → WR 59.4%
  buys_5m 10-50       → WR 36.0%
  price_1h >200%      → WR 70.5%
  price_1h -50 a 0%   → WR 48.2%
  price_1h 50-200%    → WR 16.7%
  Raydium             → WR 79.2%
  PumpSwap            → WR 42.5%
  Jupiter             → WR  0.0%
"""

import os
from utils.logger import get_logger

log = get_logger("stat_scorer")

THRESHOLD = int(os.getenv("SCORER_THRESHOLD", "50"))


def score_token(token_info: dict) -> tuple[int, bool, str]:
    """
    Evalúa un token usando patrones estadísticos.
    Retorna (score 0-100, passed, reason_str).
    """
    score = 0
    reasons: list[str] = []

    age     = token_info.get("token_age_min")
    liq     = float(token_info.get("liquidity_usd") or 0)
    mcap    = float(token_info.get("mcap_usd") or 0)
    ch1h    = token_info.get("price_change_1h")
    buys    = int(token_info.get("buys_5m") or 0)
    program = token_info.get("program", "")

    # ── Token age ────────────────────────────────────────────────────────
    if age is not None:
        if 5 <= age <= 15:
            score += 25
            reasons.append(f"+25 edad {age:.1f}min [WR 60%]")
        elif 15 < age <= 30:
            score += 15
            reasons.append(f"+15 edad {age:.1f}min [WR 51%]")
        elif age < 5:
            score -= 5
            reasons.append(f"-5 edad {age:.1f}min (muy fresco)")
        elif 30 < age <= 60:
            score -= 10
            reasons.append(f"-10 edad {age:.1f}min (maduro)")
        else:
            score -= 30
            reasons.append(f"-30 edad {age:.1f}min [WR 27% - evitar]")

    # ── Liquidez ─────────────────────────────────────────────────────────
    if liq == 0:
        score -= 25
        reasons.append("-25 sin liquidez [WR 37%]")
    elif liq < 500:
        score -= 15
        reasons.append(f"-15 liq ${liq:.0f} (demasiado baja)")
    elif 2000 <= liq <= 10000:
        score += 20
        reasons.append(f"+20 liq ${liq:.0f} [WR 48%]")
    elif 500 <= liq < 2000:
        score += 5
        reasons.append(f"+5 liq ${liq:.0f} (aceptable)")

    # ── MCap ─────────────────────────────────────────────────────────────
    if 5000 <= mcap <= 20000:
        score += 20
        reasons.append(f"+20 mcap ${mcap:.0f} [WR 53%]")
    elif mcap > 100000:
        score -= 10
        reasons.append(f"-10 mcap ${mcap:.0f} (muy alto)")
    elif 20000 < mcap <= 100000:
        score += 5
        reasons.append(f"+5 mcap ${mcap:.0f}")

    # ── Price change 1h ──────────────────────────────────────────────────
    if ch1h is not None:
        if ch1h > 200:
            score += 25
            reasons.append(f"+25 1h={ch1h:.0f}% [WR 70% - momentum fuerte]")
        elif -50 <= ch1h <= 0:
            score += 10
            reasons.append(f"+10 1h={ch1h:.0f}% [WR 48% - dip comprable]")
        elif 50 <= ch1h <= 200:
            score -= 20
            reasons.append(f"-20 1h={ch1h:.0f}% [WR 16% - ya bombeó]")
        elif ch1h < -50:
            score -= 5
            reasons.append(f"-5 1h={ch1h:.0f}% (caída fuerte)")

    # ── Buys 5m ──────────────────────────────────────────────────────────
    if buys >= 200:
        score += 25
        reasons.append(f"+25 buys_5m={buys} [WR 68% - alta presión]")
    elif buys >= 50:
        score += 15
        reasons.append(f"+15 buys_5m={buys} [WR 59%]")
    elif 10 <= buys < 50:
        score -= 5
        reasons.append(f"-5 buys_5m={buys} [WR 36% - baja actividad]")

    # ── Programa ─────────────────────────────────────────────────────────
    if program == "Raydium":
        score += 20
        reasons.append("+20 Raydium [WR 79%]")
    elif program == "PumpSwap":
        score += 5
        reasons.append("+5 PumpSwap [WR 42%]")
    elif program == "Jupiter":
        score -= 50
        reasons.append("-50 Jupiter [WR 0% - evitar siempre]")

    score = max(0, min(100, score))
    passed = score >= THRESHOLD
    reason = " | ".join(reasons) if reasons else "sin señales"

    return score, passed, reason


def should_buy(token_info: dict) -> tuple[bool, str]:
    """Interfaz simple: (comprar, motivo)."""
    score, passed, reason = score_token(token_info)
    log.info(f"[stat_scorer] score={score} {'✅ COMPRAR' if passed else '❌ SKIP'} | {reason}")
    return passed, f"score={score} | {reason}"

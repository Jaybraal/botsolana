"""
Scorer data-driven para sniping de tokens nuevos en Pump.fun.
Contrato idéntico a stat_scorer: score_token(token_info) -> (int, bool, str)

Requiere data/snipe_patterns.json — generarlo primero con:
    python3 -m data_collector.snipe_trainer
"""
import json
import os
from pathlib import Path

from utils.logger import get_logger

log = get_logger("snipe_scorer")

PATTERNS_PATH = os.getenv("PATTERNS_PATH", "data/snipe_patterns.json")
_patterns: dict | None = None


def _load() -> dict:
    global _patterns
    if _patterns is None:
        p = Path(PATTERNS_PATH)
        if not p.exists():
            raise FileNotFoundError(
                f"snipe_patterns.json no encontrado en {PATTERNS_PATH}. "
                "Ejecuta: python3 -m data_collector.snipe_trainer"
            )
        with open(p) as f:
            _patterns = json.load(f)
    return _patterns


def _match_bucket(value: float, buckets: list[dict]) -> dict | None:
    for b in buckets:
        if b["min"] <= value < b["max"]:
            return b
    return None


def score_token(
    token_info: dict, elite_signal: bool = False
) -> tuple[int, bool, str]:
    """
    Evalúa un token nuevo con patrones derivados de wallet_history.db.
    Retorna (score 0-100, passed, reason_str).
    """
    p = _load()
    score = 0
    reasons: list[str] = []

    # ── Edad del token (feature más predictiva) ──────────────────────────
    age = token_info.get("token_age_min")
    if age is not None:
        b = _match_bucket(float(age), p["age_buckets"])
        if b:
            score += b["score_pts"]
            reasons.append(
                f"+{b['score_pts']} edad {float(age):.1f}min [{b['label']} WR={b['wr']}%]"
            )

    # ── Market cap ───────────────────────────────────────────────────────
    mcap = float(token_info.get("mcap_usd") or 0)
    if mcap > 0:
        b = _match_bucket(mcap, p["mcap_buckets"])
        if b:
            score += b["score_pts"]
            reasons.append(f"+{b['score_pts']} mcap ${mcap:,.0f} [{b['label']} WR={b['wr']}%]")

    # ── Momentum (buys acumulados) ───────────────────────────────────────
    buys = int(token_info.get("buys_5m") or 0)
    if buys > 0:
        b = _match_bucket(float(buys), p["buys_buckets"])
        if b:
            score += b["score_pts"]
            reasons.append(f"+{b['score_pts']} buys={buys} [{b['label']} WR={b['wr']}%]")

    # ── Señal de wallet élite ────────────────────────────────────────────
    if elite_signal:
        boost = int(p.get("elite_wallet_boost", 15))
        score += boost
        reasons.append(f"+{boost} wallet élite compró este token")

    score = max(0, min(100, score))
    threshold = int(p.get("buy_threshold", 55))
    passed = score >= threshold
    reason = " | ".join(reasons) if reasons else "sin señales"

    return score, passed, reason


def should_buy(
    token_info: dict, elite_signal: bool = False
) -> tuple[bool, str]:
    """Interfaz simple: (comprar, motivo)."""
    score, passed, reason = score_token(token_info, elite_signal)
    log.info(
        f"[snipe_scorer] score={score} {'✅ COMPRAR' if passed else '❌ SKIP'} | {reason}"
    )
    return passed, f"score={score} | {reason}"

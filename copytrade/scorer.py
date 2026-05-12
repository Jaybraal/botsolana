"""
Scorer basado en patrones Groq aprendidos de historial on-chain.

Evalúa un token candidato contra los patrones de la wallet que lo compró.
Retorna (score 0-100, passed bool, reason str).

score >= THRESHOLD → copiar
score <  THRESHOLD → ignorar
"""

import json
import os

from utils.logger import get_logger

log = get_logger("scorer")

PATTERNS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "groq_patterns.json")
THRESHOLD     = int(os.getenv("SCORER_THRESHOLD", "40"))   # score mínimo para copiar

_patterns: dict = {}


def _load():
    global _patterns
    if _patterns:
        return
    if not os.path.exists(PATTERNS_PATH):
        log.warning("groq_patterns.json no encontrado — scorer desactivado")
        return
    with open(PATTERNS_PATH) as f:
        _patterns = json.load(f)
    log.info(f"Scorer: patrones cargados para {[k for k in _patterns if not k.startswith('_')]}")


def _check_condition(value, condition: str, threshold) -> bool:
    if value is None:
        return False
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if condition == "menor_que":
        return v < float(threshold)
    if condition == "mayor_que":
        return v > float(threshold)
    if condition == "entre" and isinstance(threshold, list) and len(threshold) == 2:
        return float(threshold[0]) <= v <= float(threshold[1])
    if condition == "igual":
        return v == float(threshold)
    return False


def _feature_value(token_info: dict, feature: str):
    """Mapea nombre de feature de Groq al campo real del token."""
    mapping = {
        "edad_token":        "token_age_min",
        "token_age_min":     "token_age_min",
        "liquidez":          "liquidity_usd",
        "liquidity_usd":     "liquidity_usd",
        "mcap":              "mcap_usd",
        "mcap_usd":          "mcap_usd",
        "cambio_precio_5m":  "price_change_5m",
        "price_change_5m":   "price_change_5m",
        "cambio_precio_1h":  "price_change_1h",
        "price_change_1h":   "price_change_1h",
        "compras_5m":        "buys_5m",
        "buys_5m":           "buys_5m",
        "ventas_5m":         "sells_5m",
        "sells_5m":          "sells_5m",
        "sol_gastado":       "sol_spent",
        "sol_spent":         "sol_spent",
    }
    key = mapping.get(feature.lower(), feature.lower())
    return token_info.get(key)


def score_token(wallet_label: str, token_info: dict) -> tuple[int, bool, str]:
    """
    Evalúa token_info contra los patrones de wallet_label.

    token_info debe contener los campos de DexScreener:
        token_age_min, liquidity_usd, mcap_usd, price_change_5m,
        price_change_1h, buys_5m, sells_5m, program

    Retorna: (score 0-100, passed, reason)
    """
    _load()

    if not _patterns:
        return (50, True, "scorer desactivado — sin patrones")

    # Buscar patrón de la wallet — exact, case-insensitive, y limpiando emojis/espacios
    wallet_pattern = _patterns.get(wallet_label)
    if not wallet_pattern:
        # Normalizar: quitar emojis y espacios extra para match (ej: "Cupsey ⭐" → "Cupsey")
        label_clean = wallet_label.encode("ascii", "ignore").decode().strip()
        for k, v in _patterns.items():
            if k.lower() == wallet_label.lower() or k.lower() == label_clean.lower():
                wallet_pattern = v
                break

    if not wallet_pattern:
        return (50, True, f"sin patrón para {wallet_label} — dejando pasar")

    patterns = wallet_pattern.get("patterns", {})
    buy_signals    = patterns.get("buy_signals", [])
    avoid_signals  = patterns.get("avoid_signals", [])
    best_program   = patterns.get("best_program", "")
    ideal_age      = patterns.get("ideal_token_age_min", {})
    ideal_liq      = patterns.get("ideal_liquidity_usd", {})

    score    = 50   # base neutro
    reasons  = []

    # ── Buy signals (suman confianza ponderada) ───────────────────────────────
    for sig in buy_signals:
        val = _feature_value(token_info, sig["feature"])
        if _check_condition(val, sig["condition"], sig["value"]):
            pts = round(sig["confidence"] * 0.3)
            score += pts
            reasons.append(f"+{pts} {sig['feature']} {sig['condition']} {sig['value']}")

    # ── Avoid signals (restan confianza ponderada) ────────────────────────────
    for sig in avoid_signals:
        val = _feature_value(token_info, sig["feature"])
        if _check_condition(val, sig["condition"], sig["value"]):
            pts = round(sig["confidence"] * 0.4)
            score -= pts
            reasons.append(f"-{pts} EVITAR: {sig['description'][:50]}")

    # ── Bonus por programa ideal ──────────────────────────────────────────────
    if best_program and token_info.get("program", "") == best_program:
        score += 10
        reasons.append(f"+10 programa ideal ({best_program})")

    # ── Bonus por rango de edad ideal ─────────────────────────────────────────
    age = token_info.get("token_age_min")
    if age is not None and ideal_age.get("max"):
        if age <= ideal_age["max"]:
            score += 8
            reasons.append(f"+8 edad {age:.1f}min <= max {ideal_age['max']}min")
        else:
            score -= 15
            reasons.append(f"-15 edad {age:.1f}min > max {ideal_age['max']}min")

    # ── Bonus por rango de liquidez ideal ─────────────────────────────────────
    liq = token_info.get("liquidity_usd")
    if liq is not None:
        liq_min = ideal_liq.get("min", 0) or 0
        liq_max = ideal_liq.get("max", float("inf")) or float("inf")
        if liq_min <= liq <= liq_max:
            score += 5
            reasons.append(f"+5 liquidez ${liq:.0f} en rango ideal")
        elif liq < liq_min:
            score -= 10
            reasons.append(f"-10 liquidez ${liq:.0f} < min ${liq_min:.0f}")

    score = max(0, min(100, score))
    passed = score >= THRESHOLD
    reason = " | ".join(reasons) if reasons else "sin señales"

    return (score, passed, reason)


def should_copy(wallet_label: str, token_info: dict) -> tuple[bool, str]:
    """Interfaz simplificada: True si debe copiarse."""
    score, passed, reason = score_token(wallet_label, token_info)
    log.info(f"[scorer] {wallet_label} → score={score} {'✅ COPIAR' if passed else '❌ SKIP'} | {reason}")
    return passed, f"score={score} | {reason}"

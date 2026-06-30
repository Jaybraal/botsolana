"""
Learner-Driven Scanner — opera sin copywallet.

Ciclo cada LEARNER_SCAN_INTERVAL_MIN minutos:
  1. DexScreener /token-boosts/top/v1 → lista de tokens trending Solana
  2. get_tokens_batch() → datos completos por mint
  3. _passes_learner_criteria() → filtro por learner_rules_copywallet.json
  4. PumpPortal API → validar precio en tiempo real
  5. stat_scorer + learner_rules → doble filtro de scoring
  6. execute_copy() → abrir posición en simulator/executor
  7. _monitor_position() → SL/TP/trailing/timeout

Variables de entorno:
  LEARNER_SCANNER_ENABLED   = true   # activar/desactivar
  LEARNER_SCAN_INTERVAL_MIN = 5      # minutos entre scans DexScreener
  LEARNER_SCORE_THRESHOLD   = 55     # threshold stat_scorer
  LEARNER_CRITERIA_MATCH    = 5      # criterios learner_rules que deben coincidir (de 7)
  MAX_AUTO_POSITIONS        = 2      # máximo posiciones simultáneas
  AUTO_STOP_LOSS_PCT        = -8     # % para stop loss
  AUTO_TAKE_PROFIT_PCT      = 25     # % para take profit
  AUTO_TRAILING_PEAK        = 15     # % para activar trailing
  AUTO_TRAILING_DROP        = 7      # % de caída desde pico → vender
  AUTO_MAX_HOLD_MIN         = 7      # minutos máximos por posición
"""

import asyncio
import json
import os
import time

import httpx

from copytrade.executor import execute_copy
from copytrade.learner import load_rules
from copytrade.stat_scorer import score_token as stat_score
from config import TOKENS
from utils.dexscreener import get_trending_solana, get_tokens_batch
from utils.logger import get_logger

log = get_logger("learner_scanner")

SOL_MINT       = TOKENS["SOL"]
PUMPPORTAL_API = "https://pumpportal.fun/api/coin-data"

# ── Config ────────────────────────────────────────────────────────────────────
ENABLED        = os.getenv("LEARNER_SCANNER_ENABLED", "true").lower() == "true"
SCAN_INTERVAL  = float(os.getenv("LEARNER_SCAN_INTERVAL_MIN", "5")) * 60
SCORE_THRESH   = int(os.getenv("LEARNER_SCORE_THRESHOLD", "55"))
CRITERIA_MATCH = int(os.getenv("LEARNER_CRITERIA_MATCH", "5"))
MAX_POSITIONS  = int(os.getenv("MAX_AUTO_POSITIONS", "2"))
STOP_LOSS_PCT  = float(os.getenv("AUTO_STOP_LOSS_PCT",   "-8"))
TAKE_PROFIT    = float(os.getenv("AUTO_TAKE_PROFIT_PCT", "25"))
TRAIL_PEAK     = float(os.getenv("AUTO_TRAILING_PEAK",   "15"))
TRAIL_DROP     = float(os.getenv("AUTO_TRAILING_DROP",    "7"))
MAX_HOLD_MIN   = float(os.getenv("AUTO_MAX_HOLD_MIN",     "7"))
MONITOR_TICK   = 10  # segundos entre checks de precio

# Criterios hardcoded — fallback si learner_rules_copywallet.json no existe
_FALLBACK_RULES = {
    "scoring_rules": {
        "min_mcap_usd":      15571.0,
        "max_mcap_usd":      46714.0,
        "min_liquidity_usd":  3127.0,
        "min_buy_pressure":   0.513,
        "min_change_1h_pct":  78.98,
        "max_age_days":        7.3,
    }
}

# Estado en memoria — {mint: {entry_price_usd, entry_time, peak_pct, symbol, program}}
_auto_positions: dict[str, dict] = {}

# Caché SOL price
_sol_price_usd: float = 150.0
_sol_price_ts:  float = 0.0


def _get_sol_price() -> float:
    global _sol_price_usd, _sol_price_ts
    if time.time() - _sol_price_ts < 60:
        return _sol_price_usd
    try:
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
            timeout=3,
        )
        if r.status_code == 200:
            _sol_price_usd = float(r.json()["solana"]["usd"])
            _sol_price_ts  = time.time()
    except Exception:
        pass
    return _sol_price_usd


# ── Criterios y scoring ───────────────────────────────────────────────────────

def _passes_learner_criteria(token_info: dict, rules: dict) -> tuple[bool, str]:
    """
    Verifica si token_info cumple los criterios de learner_rules_copywallet.json.
    Si rules está vacío, usa _FALLBACK_RULES hardcoded.
    Retorna (passed, reason).
    """
    scoring = (rules or _FALLBACK_RULES).get("scoring_rules", _FALLBACK_RULES["scoring_rules"])

    checks  = 0
    passed  = 0
    reasons = []

    def _check(label: str, value, threshold, is_max: bool = False) -> bool:
        nonlocal checks, passed
        if value is None or threshold is None:
            return True  # sin dato → no penalizar
        checks += 1
        ok = (value <= threshold) if is_max else (value >= threshold)
        if ok:
            passed += 1
        else:
            reasons.append(f"{label}: {value:.1f} {'>' if is_max else '<'} {threshold:.1f}")
        return ok

    _check("mcap_min",    token_info.get("mcap_usd"),        scoring.get("min_mcap_usd"))
    _check("mcap_max",    token_info.get("mcap_usd"),        scoring.get("max_mcap_usd"),     is_max=True)
    _check("liquidity",   token_info.get("liquidity_usd"),   scoring.get("min_liquidity_usd"))
    _check("buy_press",   token_info.get("buy_pressure"),    scoring.get("min_buy_pressure"))
    _check("change_1h",   token_info.get("price_change_1h"), scoring.get("min_change_1h_pct"))
    _check("age_days",    token_info.get("age_days"),        scoring.get("max_age_days"),      is_max=True)

    if checks == 0:
        return True, "sin criterios cargados — dejando pasar"

    ok = passed >= min(CRITERIA_MATCH, checks)
    reason = f"{passed}/{checks} criterios" if ok else f"solo {passed}/{checks}: {', '.join(reasons)}"
    return ok, reason


def _score_and_decide(token_info: dict) -> tuple[bool, str]:
    """
    Doble filtro: stat_scorer (primera capa) + learner_criteria (segunda capa).
    Retorna (passed, reason).
    """
    if not token_info.get("price_usd"):
        return False, "precio USD = 0 — descartado"

    # Primera capa: stat_scorer
    score, _stat_passed, stat_reason = stat_score(token_info)
    if score < SCORE_THRESH:
        return False, f"stat_scorer score={score} < {SCORE_THRESH} | {stat_reason}"

    # Segunda capa: learner_rules_copywallet
    rules = load_rules(source="CW")
    crit_passed, crit_reason = _passes_learner_criteria(token_info, rules)
    if not crit_passed:
        return False, f"learner_criteria FAIL | {crit_reason}"

    return True, f"stat={score} | {stat_reason} | criteria: {crit_reason}"

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


# ── Precio PumpPortal ─────────────────────────────────────────────────────────

def _fetch_pumpportal_price(mint: str) -> dict | None:
    """Precio desde bonding curve de PumpPortal — fallback cuando DexScreener no tiene el token."""
    try:
        r = httpx.get(PUMPPORTAL_API, params={"mint": mint}, timeout=3)
        if r.status_code != 200:
            return None
        d = r.json()
        v_sol = float(d.get("virtual_sol_reserves") or 0)
        v_tok = float(d.get("virtual_token_reserves") or 0)
        if v_sol <= 0 or v_tok <= 0:
            return None
        price_sol = (v_sol / 1e9) / (v_tok / 1e6)
        price_usd = price_sol * _get_sol_price()
        return {"price_usd": price_usd, "price_sol": price_sol}
    except Exception:
        return None


def _fetch_current_price(mint: str, pair_address: str = "") -> float:
    """Precio actual — DexScreener pair → PumpPortal fallback → 0."""
    from utils.dexscreener import get_pair_price
    if pair_address:
        price = get_pair_price(pair_address)
        if price and price > 0:
            return price
    pp = _fetch_pumpportal_price(mint)
    return (pp or {}).get("price_usd", 0)


# ── Posiciones ────────────────────────────────────────────────────────────────

_SIM_POSITIONS_PATH = "data/sim_positions.json"


def _recover_orphan_positions() -> None:
    """Recupera posiciones de AUTO 🤖 desde sim_positions.json tras un restart."""
    if not os.path.exists(_SIM_POSITIONS_PATH):
        return
    try:
        with open(_SIM_POSITIONS_PATH) as f:
            all_pos = json.load(f)
        recovered = 0
        for mint, pos in all_pos.items():
            if not pos or pos.get("wallet") != "AUTONOMOUS_BOT":
                continue
            if mint in _auto_positions:
                continue
            entry_price = pos.get("entry_price", 0)
            if not entry_price:
                continue
            _auto_positions[mint] = {
                "entry_price_usd": entry_price,
                "last_price_usd":  entry_price,
                "entry_time":      pos.get("opened_at", time.time()),
                "peak_pct":        0.0,
                "symbol":          pos.get("symbol", mint[:6]),
                "program":         (pos.get("entry_context") or {}).get("dex_id", "PumpSwap"),
                "pair_address":    pos.get("pair_address", ""),
            }
            recovered += 1
        if recovered:
            log.info(f"[learner] ♻️  {recovered} posiciones huérfanas recuperadas")
    except Exception as e:
        log.warning(f"[learner] No se pudieron recuperar posiciones huérfanas: {e}")


async def _trigger_sell(mint: str, symbol: str, current_price: float, reason: str, program: str):
    """Envía señal de venta al executor/simulator y limpia la posición."""
    if mint not in _auto_positions:
        return
    if current_price <= 0:
        current_price = _auto_positions[mint].get("last_price_usd", 0)
    _auto_positions.pop(mint, None)

    sol_price = _get_sol_price()
    price_sol = (current_price / sol_price) if current_price > 0 and sol_price > 0 else 0.0

    sell_swap = {
        "wallet":            "AUTONOMOUS_BOT",
        "wallet_label":      "AUTO 🤖",
        "program":           program,
        "token_in":          mint,
        "token_out":         SOL_MINT,
        "symbol_in":         symbol,
        "symbol_out":        "SOL",
        "amount_in":         0,
        "amount_out":        0,
        "wallet_pre_sol":    0,
        "implied_price_sol": price_sol,
    }
    log.info(f"[learner] 🔴 VENTA {symbol} | motivo: {reason}")
    await execute_copy(sell_swap)


async def _monitor_position(mint: str, symbol: str):
    """Monitorea precio cada MONITOR_TICK segundos. Aplica SL/TP/trailing/timeout."""
    pos = _auto_positions.get(mint)
    if not pos:
        return

    entry_price  = pos["entry_price_usd"]
    entry_time   = pos["entry_time"]
    program      = pos["program"]
    pair_address = pos.get("pair_address", "")

    log.info(
        f"[learner] 👁 Monitor {symbol} | entrada ${entry_price:.8f} | "
        f"SL {STOP_LOSS_PCT:+.0f}% | TP +{TAKE_PROFIT:.0f}% | "
        f"trailing >{TRAIL_PEAK:.0f}% cae -{TRAIL_DROP:.0f}% | max {MAX_HOLD_MIN:.0f}min"
    )

    while mint in _auto_positions:
        await asyncio.sleep(MONITOR_TICK)
        if mint not in _auto_positions:
            break

        current = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_current_price, mint, pair_address
        )

        if current <= 0:
            current = _auto_positions[mint].get("last_price_usd", 0)
        else:
            _auto_positions[mint]["last_price_usd"] = current

        hold_min = (time.time() - entry_time) / 60

        if current <= 0 or entry_price <= 0:
            if hold_min >= MAX_HOLD_MIN:
                await _trigger_sell(mint, symbol, 0.0, f"timeout-sin-precio {hold_min:.1f}min", program)
            continue

        pnl_pct  = (current - entry_price) / entry_price * 100
        peak_pct = _auto_positions[mint].get("peak_pct", 0)

        if pnl_pct > peak_pct:
            _auto_positions[mint]["peak_pct"] = pnl_pct
            peak_pct = pnl_pct

        log.info(f"[learner] 📊 {symbol} | P&L {pnl_pct:+.1f}% | pico {peak_pct:+.1f}% | hold {hold_min:.1f}min")

        exit_reason = None
        if pnl_pct <= STOP_LOSS_PCT:
            exit_reason = f"stop-loss {pnl_pct:+.1f}%"
        elif pnl_pct >= TAKE_PROFIT:
            exit_reason = f"take-profit {pnl_pct:+.1f}%"
        elif peak_pct >= TRAIL_PEAK and (peak_pct - pnl_pct) >= TRAIL_DROP:
            exit_reason = f"trailing pico={peak_pct:+.1f}% actual={pnl_pct:+.1f}%"
        elif hold_min >= MAX_HOLD_MIN:
            exit_reason = f"timeout {hold_min:.1f}min"

        if exit_reason:
            await _trigger_sell(mint, symbol, current, exit_reason, program)
            break


async def _open_position(mint: str, token_info: dict, reason: str):
    """Registra posición y ejecuta compra via executor/simulator."""
    if len(_auto_positions) >= MAX_POSITIONS:
        log.debug(f"[learner] Límite {MAX_POSITIONS} posiciones — skip {mint[:8]}")
        return
    if mint in _auto_positions:
        return

    entry_price = token_info.get("price_usd", 0)
    symbol      = token_info.get("symbol", mint[:6])
    program     = token_info.get("program", "PumpSwap")
    sol_price   = _get_sol_price()

    _auto_positions[mint] = {
        "entry_price_usd": entry_price,
        "last_price_usd":  entry_price,
        "entry_time":      time.time(),
        "peak_pct":        0.0,
        "symbol":          symbol,
        "program":         program,
        "pair_address":    token_info.get("pair_address", ""),
    }

    buy_swap = {
        "wallet":            "AUTONOMOUS_BOT",
        "wallet_label":      "AUTO 🤖",
        "program":           program,
        "token_in":          SOL_MINT,
        "token_out":         mint,
        "symbol_in":         "SOL",
        "symbol_out":        symbol,
        "amount_in":         0,
        "amount_out":        0,
        "wallet_pre_sol":    0,
        "implied_price_sol": entry_price / sol_price if sol_price > 0 else 0,
    }

    log.info(f"[learner] 🟢 COMPRA {symbol} | {reason}")
    await execute_copy(buy_swap)
    asyncio.create_task(_monitor_position(mint, symbol))

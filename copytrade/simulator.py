"""
Simulador de P&L realista — replica exactamente lo que pasaría en live.

Reglas de realismo:
- Capital inicial = SIM_CAPITAL (valor real en USD de tu wallet)
- Trade size = mismo % que en live: MAX_TRADE_PCT + SCALING_TIERS de config.py
- Slippage = SIM_SLIPPAGE_PCT por leg (compra + venta) — defecto 1.5% (realista para trades <$100)
- Fee = SIM_PRIORITY_FEE_SOL * precio_sol (round-trip real)
- El balance compone igual que en live: a mayor ganancia, mayor % por trade
"""

import json
import math
import os
import random
import time
import threading
from datetime import datetime

from config import MAX_TRADE_PCT, SCALING_TIERS, get_max_trade_pct_by_balance
from utils.dexscreener import get_best_pair
from utils.market_context import get_context
from utils.logger import get_logger

# Scorer: misma lógica que en live mode
_USE_SCORER = os.getenv("USE_GROQ_SCORER", "true").lower() == "true"

# Caché del precio de SOL en USD — se refresca cada 60s
_sol_price_usd:       float = 0.0
_sol_price_fetched_at: float = 0.0

def _get_sol_price_usd() -> float:
    global _sol_price_usd, _sol_price_fetched_at
    if time.time() - _sol_price_fetched_at < 60 and _sol_price_usd > 0:
        return _sol_price_usd
    try:
        pair = get_best_pair("So11111111111111111111111111111111111111112")
        if pair:
            _sol_price_usd = float(pair.get("priceUsd") or 0)
            _sol_price_fetched_at = time.time()
    except Exception:
        pass
    return _sol_price_usd if _sol_price_usd > 0 else 150.0

log = get_logger("simulator")

os.makedirs("data", exist_ok=True)
POSITIONS_FILE = "data/sim_positions.json"
HISTORY_FILE   = "data/sim_history.json"
BALANCE_FILE   = "data/sim_balance.json"
DRIFT_LOG_FILE = "data/execution_drift.jsonl"

# Si SIM_RESET=true, borra datos previos y empieza desde SIM_CAPITAL limpio.
# Poner en false después del primer deploy para que no resetee en reinicios.
if os.getenv("SIM_RESET", "false").lower() == "true":
    for _f in [POSITIONS_FILE, HISTORY_FILE, BALANCE_FILE]:
        if os.path.exists(_f):
            os.remove(_f)

# Capital inicial configurado en .env (default $45)
SIM_INITIAL_CAPITAL  = float(os.getenv("SIM_CAPITAL",         "50.0"))
SIM_MIN_TRADE        = float(os.getenv("SIM_MIN_TRADE",        "0.50"))   # mínimo $0.50 por trade
SIM_LIQUIDATION      = float(os.getenv("SIM_LIQUIDATION",      "2.0"))    # pausar si balance < $2
SIM_PRIORITY_FEE_SOL = float(os.getenv("SIM_PRIORITY_FEE_SOL", "0.0004")) # 0.0002 SOL × 2 round-trip
SIM_SLIPPAGE_PCT     = float(os.getenv("SIM_SLIPPAGE_PCT",      "0.015"))  # 1.5% por leg — realista para trades <$100 en Pump.fun
SIM_MAX_HOLD_MIN     = float(os.getenv("SIM_MAX_HOLD_MIN",      "10000"))  # auto-close si la wallet no vende en N minutos (10000 = permite hold indefinido)
SIM_MAX_CONFIRMATIONS = int(os.getenv("SIM_MAX_CONFIRMATIONS",  "3"))      # max wallets que pueden escalar la misma posición

# Realismo brutal — 5 mejoras cuantitativas
SIM_DYNAMIC_LIQUIDITY_LIMIT = os.getenv("SIM_DYNAMIC_LIQUIDITY_LIMIT", "true").lower() == "true"
SIM_DYNAMIC_SLIPPAGE        = os.getenv("SIM_DYNAMIC_SLIPPAGE", "true").lower() == "true"
SIM_MARKET_IMPACT           = os.getenv("SIM_MARKET_IMPACT", "true").lower() == "true"
SIM_SMART_FAIL_RATE         = os.getenv("SIM_SMART_FAIL_RATE", "true").lower() == "true"
SIM_BASE_FAIL_RATE          = float(os.getenv("SIM_BASE_FAIL_RATE", "0.08"))  # 8% baseline
SIM_EXTENDED_METRICS        = os.getenv("SIM_EXTENDED_METRICS", "true").lower() == "true"

# Tiers de tamaño por trade según balance — escala progresivamente conforme crece el capital.
# (balance_mínimo, tope_usd_por_trade)
TRADE_CAP_TIERS: list[tuple[float, float]] = [
    (0,     5.0),   # $0–$100    → $5/trade
    (100,  15.0),   # $100–$300  → $15/trade
    (300,  35.0),   # $300–$600  → $35/trade
    (600,  60.0),   # $600–$1k   → $60/trade
    (1000, 90.0),   # $1k–$2k    → $90/trade
    (2000, 130.0),  # $2k–$5k    → $130/trade
    (5000, 200.0),  # $5k+       → $200/trade
]

# Tokens que son "dinero" (SOL, USDC, USDT)
STABLE_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


# ── Persistencia ──────────────────────────────────────────────────────────────

def _load_positions() -> dict:
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _load_history() -> list:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _load_balance() -> float:
    if os.path.exists(BALANCE_FILE):
        try:
            with open(BALANCE_FILE) as f:
                return float(json.load(f).get("balance", SIM_INITIAL_CAPITAL))
        except Exception:
            pass
    return SIM_INITIAL_CAPITAL


def _save_positions():
    with open(POSITIONS_FILE, "w") as f:
        json.dump(_positions, f, indent=2)


def _save_history():
    with open(HISTORY_FILE, "w") as f:
        json.dump(_history, f, indent=2)


def _save_balance():
    with open(BALANCE_FILE, "w") as f:
        json.dump({
            "balance":       round(_sim_balance, 4),
            "initial":       SIM_INITIAL_CAPITAL,
            "updated_at":    datetime.now().strftime("%H:%M:%S %d/%m/%Y"),
        }, f, indent=2)


# ── Estado en memoria ─────────────────────────────────────────────────────────

_positions:   dict[str, dict] = _load_positions()  # {token_mint: position}
_history:     list[dict]                 = _load_history()
_sim_balance: float                      = _load_balance()
_lock = threading.Lock()  # protege _positions contra race conditions

# Contadores del scorer — cuántos trades filtra vs deja pasar
_scorer_accepted = 0
_scorer_rejected = 0


# ── Capital dinámico ──────────────────────────────────────────────────────────

def _get_trade_pct() -> float:
    """Retorna % máximo por trade según balance actual en USD."""
    return get_max_trade_pct_by_balance(_sim_balance)


def _get_trade_cap_usd() -> float:
    """Tope por trade según balance — a mayor capital, menor % efectivo por trade."""
    cap = TRADE_CAP_TIERS[0][1]
    for min_balance, max_usd in TRADE_CAP_TIERS:
        if _sim_balance >= min_balance:
            cap = max_usd
    return cap


def _get_trade_amount() -> float:
    amount = _sim_balance * _get_trade_pct()
    return min(amount, _get_trade_cap_usd())


# ── Realismo brutal: helpers cuantitativos ─────────────────────────────────

def _get_dynamic_liquidity_limit(liquidity_usd: float) -> float:
    """Ratio dinámico según tamaño del pool — más conservador en microcaps."""
    if not SIM_DYNAMIC_LIQUIDITY_LIMIT or liquidity_usd <= 0:
        return 0.01
    if liquidity_usd < 10000:
        return 0.003   # 0.3% en pools muy pequeños
    elif liquidity_usd < 50000:
        return 0.007   # 0.7% en pools medianos
    else:
        return 0.010   # 1.0% en pools grandes


def _calc_slippage_dynamic(trade_usd: float, liquidity_usd: float, is_sell: bool = False) -> float:
    """Slippage dinámico basado en ratio trade/liquidity.
    BUY cap: 30%, SELL cap: 50% (vender es peor).
    """
    if not SIM_DYNAMIC_SLIPPAGE or liquidity_usd <= 0:
        return SIM_SLIPPAGE_PCT

    impact_ratio = trade_usd / liquidity_usd
    dynamic = SIM_SLIPPAGE_PCT + impact_ratio * 0.5

    cap = 0.50 if is_sell else 0.30
    return min(dynamic, cap)


def _calc_market_impact(trade_usd: float, liquidity_usd: float) -> float:
    """Market impact NO lineal (raíz cuadrada) — más realista en AMMs.
    Impacto cresce exponencialmente, no linealmente.
    """
    if not SIM_MARKET_IMPACT or liquidity_usd <= 0:
        return 0.0

    ratio = trade_usd / liquidity_usd
    impact = math.sqrt(ratio) * 0.35
    return min(impact, 0.40)  # cap 40%


def _calc_fail_rate(liquidity_usd: float, volatility_pct: float = 10.0) -> float:
    """TX fail rate inteligente basado en liquidez + volatilidad.
    Pools pequeños y mercados volátiles → más fallos.
    """
    if not SIM_SMART_FAIL_RATE:
        return SIM_BASE_FAIL_RATE

    # Penalidad por liquidez baja
    liq_penalty = 0.0
    if liquidity_usd < 10000:
        liq_penalty = 0.05  # +5%
    elif liquidity_usd < 50000:
        liq_penalty = 0.02  # +2%

    # Penalidad por volatilidad
    vol_penalty = max(0, (volatility_pct - 5.0) / 100) * 0.03  # hasta +3%

    fail_rate = SIM_BASE_FAIL_RATE + liq_penalty + vol_penalty
    return min(fail_rate, 0.20)  # cap 20%


def _get_price(mint: str) -> float | None:
    pair = get_best_pair(mint)
    if not pair:
        return None
    try:
        return float(pair.get("priceUsd") or 0) or None
    except (ValueError, TypeError):
        return None


# ── Entrada pública ───────────────────────────────────────────────────────────

def process(swap: dict):
    """
    Analiza un swap y actualiza posiciones simuladas.
    Llamar desde executor.execute_copy() en cada swap detectado.
    """
    global _sim_balance

    wallet       = swap.get("wallet", "")
    wallet_label = swap.get("wallet_label", wallet[:8] + "...")
    token_in     = swap.get("token_in",  "")
    token_out    = swap.get("token_out", "")
    symbol_in    = swap.get("symbol_in",  "?")
    symbol_out   = swap.get("symbol_out", "?")

    is_buy  = token_in  in STABLE_MINTS and token_out not in STABLE_MINTS
    is_sell = token_out in STABLE_MINTS and token_in  not in STABLE_MINTS

    wallet_buy_time = swap.get("wallet_buy_time")

    # Precio implícito de compra — PumpPortal (UI) o calculado desde Helius
    implied_price: float = 0.0
    if is_buy:
        if swap.get("implied_price_sol", 0) > 0:
            implied_price = swap["implied_price_sol"] * _get_sol_price_usd()
        elif swap.get("amount_in", 0) > 0 and swap.get("amount_out", 0) > 0:
            sol_amount   = swap["amount_in"] / 1_000_000_000
            token_amount = swap["amount_out"] / 1_000_000
            if token_amount > 0:
                implied_price = (sol_amount / token_amount) * _get_sol_price_usd()

    # Precio implícito de venta — precio REAL al que vendió la wallet (más preciso que DexScreener)
    sell_implied: float = 0.0
    if is_sell:
        if swap.get("implied_price_sol", 0) > 0:
            sell_implied = swap["implied_price_sol"] * _get_sol_price_usd()
        elif swap.get("amount_out", 0) > 0 and swap.get("amount_in", 0) > 0:
            sol_received = swap["amount_out"] / 1_000_000_000
            token_amount = swap["amount_in"] / 1_000_000
            if token_amount > 0:
                sell_implied = (sol_received / token_amount) * _get_sol_price_usd()

    program = swap.get("program", "")

    if is_buy:
        _handle_buy(wallet, wallet_label, token_out, symbol_out, wallet_buy_time, implied_price, program)
    elif is_sell:
        _handle_sell(wallet, wallet_label, token_in, symbol_in, sell_implied)


# ── Compra ────────────────────────────────────────────────────────────────────

def _auto_close_stale():
    """Cierra posiciones abiertas hace más de SIM_MAX_HOLD_MIN minutos sin señal de venta."""
    now = time.time()
    stale = [
        (mint, pos) for mint, pos in list(_positions.items())
        if pos.get("opened_at") and (now - pos["opened_at"]) / 60 > SIM_MAX_HOLD_MIN
        and pos.get("entry_price")  # ignorar placeholders vacíos
    ]
    for mint, pos in stale:
        symbol = pos.get("symbol", mint[:6])
        label  = pos.get("wallet_label", "?")
        price  = _get_price(mint) or pos["entry_price"]  # precio actual o entrada si no hay
        log.info(
            f"[SIM] ⏰ AUTO-CLOSE [yellow]{symbol}[/] — "
            f"abierta {SIM_MAX_HOLD_MIN:.0f}+ min sin señal de venta → cierre forzado"
        )
        _handle_sell(label, label, mint, symbol, price)


def _handle_buy(wallet: str, label: str, token_mint: str, symbol: str,
                wallet_buy_time: float | None = None, implied_price: float = 0.0,
                program: str = ""):
    """Abre posición simulada al precio actual usando % del balance."""
    global _sim_balance, _scorer_accepted, _scorer_rejected

    # Cerrar posiciones que llevan demasiado tiempo abiertas sin señal de venta
    _auto_close_stale()

    with _lock:
        existing = _positions.get(token_mint)

        # Posición ya abierta con precio real → escalar si nueva wallet confirma
        if existing and existing.get("entry_price"):
            confirmations = existing.get("confirmations", 1)
            if confirmations < SIM_MAX_CONFIRMATIONS and _sim_balance > SIM_LIQUIDATION:
                extra = round(_get_trade_amount() * 0.5, 4)
                old_amount = existing["amount_usd"]
                old_price = existing["entry_price"]
                new_amount = round(old_amount + extra, 4)
                # Promediar el precio de entrada según el tamaño de cada tranche
                new_price = (old_price * old_amount + implied_price * extra) / new_amount if new_amount > 0 else old_price
                existing["amount_usd"]    = new_amount
                existing["entry_price"]   = round(new_price, 10)  # precisión para precios pequeños
                existing["confirmations"] = confirmations + 1
                _save_positions()
                log.info(
                    f"[SIM] 🔥 CONFIRMACIÓN #{confirmations + 1} | "
                    f"[cyan]{label}[/] también compró [yellow]{symbol}[/] | "
                    f"añadido [green]+${extra:.2f}[/] → posición total: "
                    f"[green]${new_amount:.2f}[/] (precio promedio: ${new_price:.10f})"
                )
            return

        # Placeholder o posición sin precio → ya en proceso, ignorar
        if existing:
            return

        # Reservar el slot inmediatamente para evitar race conditions
        _positions[token_mint] = {}  # placeholder

    # Balance demasiado bajo — simular liquidación
    if _sim_balance < SIM_LIQUIDATION:
        log.warning(
            f"[SIM] ⚠ Balance simulado [red]${_sim_balance:.2f}[/] < "
            f"mínimo ${SIM_LIQUIDATION} — trade cancelado"
        )
        return

    trade_amount = _get_trade_amount()
    if trade_amount < SIM_MIN_TRADE:
        log.debug(f"[SIM] Trade amount ${trade_amount:.2f} < mínimo ${SIM_MIN_TRADE} — ignorando")
        return

    detected_at = time.time()
    opened_at = wallet_buy_time if wallet_buy_time else detected_at
    latency_s = detected_at - opened_at if wallet_buy_time else 0

    # ⚠️ LATENCY DELAY SIMULATION (crítico para realismo)
    # En live trading, hay un delay mínimo de 1.5s entre detección y ejecución:
    # - 0.5-1s: detectar + procesar en Watcher
    # - 0.2s: validaciones en Executor
    # - 0.5s: pedir quote a Jupiter/PumpPortal
    # - 0.3s: firmar y enviar TX
    # En ese tiempo, el precio SUBE en bonding curve (compra más cara)
    REALISTIC_LATENCY_S = 1.5  # segundos reales
    price_rise_per_second = 0.015  # 1.5% por segundo en BC durante pump inicial
    latency_price_impact = REALISTIC_LATENCY_S * price_rise_per_second  # cuánto sube el precio

    # Precio: DexScreener primero (precio actual de mercado), implied como fallback.
    dex_price = _get_price(token_mint)
    if dex_price:
        # Incluso si usamos DexScreener (token indexado), el precio subió desde que Theo compró
        # Aplicamos el impacto de latencia realista
        price = dex_price * (1 + latency_price_impact)
        log.debug(
            f"[SIM] {symbol} — precio ajustado por latencia real {REALISTIC_LATENCY_S:.1f}s: "
            f"+{latency_price_impact*100:.1f}% (DexScreener)"
        )
    elif implied_price:
        # El token aún no está en DexScreener — usamos el precio del swap de la wallet.
        # Pero ese precio es de hace `latency_s` segundos; en live compraríamos más caro.
        # Sumamos: latencia de detección + latencia realista de ejecución
        total_latency = latency_s + REALISTIC_LATENCY_S if latency_s > 0 else REALISTIC_LATENCY_S
        latency_penalty = min(total_latency * price_rise_per_second, 0.30)
        price = implied_price * (1 + latency_penalty)
        if latency_penalty > 0:
            log.debug(
                f"[SIM] {symbol} — precio ajustado por latencia "
                f"{total_latency:.1f}s (detección + ejecución): +{latency_penalty*100:.1f}%"
            )
    else:
        price = None
    if not price:
        log.debug(f"[SIM] No hay precio para {symbol} — no se abre posición")
        return

    entry_context = get_context(token_mint)
    tier_pct      = _get_trade_pct()

    # ── Scorer (misma lógica que en live mode) ────────────────────────────────
    if _USE_SCORER:
        try:
            from copytrade.scorer import should_copy
            age_min = None
            if entry_context and entry_context.get("age_days") is not None:
                age_min = round(entry_context["age_days"] * 1440, 1)
            token_info = {
                "token_age_min":  age_min,
                "liquidity_usd":  entry_context.get("liquidity_usd") if entry_context else None,
                "mcap_usd":       entry_context.get("mcap_usd")      if entry_context else None,
                "price_change_5m": entry_context.get("change_5m_pct") if entry_context else None,
                "price_change_1h": entry_context.get("change_1h_pct") if entry_context else None,
                "buys_5m":        None,
                "program":        program,
            }
            passed, reason = should_copy(label, token_info)
            log.info(f"[SIM] 🤖 SCORER | [cyan]{label}[/] → [yellow]{symbol}[/] | {reason}")
            if passed:
                _scorer_accepted += 1
            else:
                _scorer_rejected += 1
                total_seen = _scorer_accepted + _scorer_rejected
                log.info(
                    f"[SIM] 🚫 SCORER SKIP | [cyan]{label}[/] → [yellow]{symbol}[/] | "
                    f"{reason} | rechazados: [red]{_scorer_rejected}[/]/{total_seen} "
                    f"({_scorer_rejected/total_seen*100:.0f}% filtrado)"
                )
                with _lock:
                    _positions.pop(token_mint, None)
                return
        except Exception as _e:
            log.warning(f"[SIM] ⚠ SCORER ERROR: {_e}")

    # ⚠️ TEST #1: Límite de liquidez dinámico
    liquidity_usd = entry_context.get("liquidity_usd", 0) if entry_context else 0
    if liquidity_usd > 0:
        max_ratio = _get_dynamic_liquidity_limit(liquidity_usd)
        max_trade = liquidity_usd * max_ratio
        if trade_amount > max_trade:
            log.info(
                f"[SIM] 🛑 Trade reducido por liquidez | {symbol} | "
                f"pool ${liquidity_usd:,.0f} × {max_ratio:.1%} = ${max_trade:.2f} máx | "
                f"solicitado ${trade_amount:.2f} → [yellow]${max_trade:.2f}[/]"
            )
            trade_amount = max_trade
            if trade_amount < SIM_MIN_TRADE:
                log.debug(f"[SIM] Trade reducido quedó < mínimo ${SIM_MIN_TRADE} — cancelado")
                return

    # ⚠️ TEST #4: TX fail rate inteligente
    volatility_pct = abs(entry_context.get("change_1h_pct", 0)) if entry_context else 10.0
    fail_rate = _calc_fail_rate(liquidity_usd, volatility_pct)
    if random.random() < fail_rate:
        fee_usd = SIM_PRIORITY_FEE_SOL * _get_sol_price_usd()
        _sim_balance = max(0.0, _sim_balance - fee_usd)
        _save_balance()
        log.warning(
            f"[SIM] 💥 TX FALLIDA | [yellow]{symbol}[/] | "
            f"fail_rate {fail_rate:.1%} (liq ${liquidity_usd:,.0f}, vol {volatility_pct:+.1f}%) | "
            f"pagó fee ${fee_usd:.4f} → balance: ${_sim_balance:.2f}"
        )
        return

    pos = {
        "token_mint":      token_mint,
        "symbol":          symbol,
        "entry_price":     price,
        "amount_usd":      round(trade_amount, 4),
        "opened_at":       opened_at,
        "opened_str":      datetime.now().strftime("%H:%M:%S %d/%m"),
        "wallet":          wallet,
        "wallet_label":    label,
        "entry_context":   entry_context,
        "detection_delay": round(latency_s, 1),
    }

    _positions[token_mint] = pos
    _save_positions()

    ctx_str = ""
    if entry_context:
        mcap = entry_context.get("mcap_usd", 0)
        bp   = entry_context.get("buy_pressure", 0)
        ch1h = entry_context.get("change_1h_pct", 0)
        ctx_str = (
            f" | mcap [white]${mcap:,.0f}[/]"
            f" | bp [white]{bp:.0%}[/]"
            f" | 1h [white]{ch1h:+.1f}%[/]"
        )

    latency_str = f" | delay [white]{latency_s:.0f}s[/]" if latency_s > 0.5 else ""
    slippage_pct = _calc_slippage_dynamic(trade_amount, liquidity_usd, is_sell=False)
    slippage_cost = trade_amount * slippage_pct
    log.info(
        f"[SIM] 📥 ENTRADA | [cyan]{label}[/] compró [yellow]{symbol}[/] | "
        f"precio: [white]${price:.8f}[/] | "
        f"trade: [green]${trade_amount:.2f}[/] ({tier_pct*100:.0f}% de ${_sim_balance:.2f} balance) | "
        f"slip_entrada: [dim]-${slippage_cost:.3f} ({slippage_pct*100:.1f}%)[/]"
        f"{ctx_str}{latency_str}"
    )


# ── Venta ─────────────────────────────────────────────────────────────────────

def _handle_sell(wallet: str, label: str, token_mint: str, symbol: str,
                 implied_price: float = 0.0):
    """Cierra posición simulada, actualiza balance y calcula P&L."""
    global _sim_balance

    with _lock:
        pos = _positions.pop(token_mint, None)
    if not pos or not pos.get("entry_price"):  # ignorar placeholders vacíos
        log.debug(f"[SIM] Venta de {symbol} sin posición abierta — ignorando")
        return

    hold_sec = time.time() - pos["opened_at"]
    hold_min = hold_sec / 60

    price_exit = _get_price(token_mint)
    if not price_exit:
        if implied_price > 0:
            # Precio real de la wallet — más preciso que DexScreener para tokens nuevos
            price_exit = implied_price
        else:
            # Sin precio disponible — cerrar al precio de entrada (solo paga fees)
            price_exit = pos["entry_price"]
            log.debug(f"[SIM] {symbol} sin precio en DexScreener ni implied — cierre al precio de entrada")

    entry      = pos["entry_price"]
    amount_usd = pos["amount_usd"]
    entry_context = pos.get("entry_context", {})

    # ⚠️ TEST #2: Slippage dinámico (con cap diferenciado para SELL)
    entry_liquidity = entry_context.get("liquidity_usd", 0) if entry_context else 0
    exit_context = get_context(token_mint)
    exit_liquidity = exit_context.get("liquidity_usd", 0) if exit_context else entry_liquidity

    slippage_entry = _calc_slippage_dynamic(amount_usd, entry_liquidity, is_sell=False)
    slippage_exit = _calc_slippage_dynamic(amount_usd, exit_liquidity, is_sell=True)

    # ⚠️ TEST #3: Market impact NO lineal (raíz cuadrada) — solo en venta
    market_impact = _calc_market_impact(amount_usd, exit_liquidity)

    # Ajuste de slippage: compramos peor y vendemos peor que el precio de mercado
    entry_adj = entry * (1 + slippage_entry)
    exit_adj = price_exit * (1 - slippage_exit) * (1 - market_impact)

    pnl_pct = (exit_adj - entry_adj) / entry_adj * 100
    pnl_usd = amount_usd * pnl_pct / 100

    # Fees reales: priority fee round-trip en USD
    fee_usd = SIM_PRIORITY_FEE_SOL * _get_sol_price_usd()
    pnl_usd -= fee_usd

    won = pnl_usd > 0

    # Actualizar balance compuesto
    balance_before = _sim_balance
    _sim_balance = max(0.0, _sim_balance + pnl_usd)
    _save_balance()

    trade = {
        "symbol":          symbol,
        "token":           token_mint,
        "wallet":          wallet,
        "wallet_label":    label,
        "entry_price":     entry,
        "exit_price":      price_exit,
        "pnl_pct":         round(pnl_pct,    2),
        "pnl_usd":         round(pnl_usd,    2),
        "amount_usd":      round(amount_usd, 4),
        "hold_min":        round(hold_min,   1),
        "won":             won,
        "balance_before":  round(balance_before, 4),
        "balance_after":   round(_sim_balance,   4),
        "opened_str":      pos["opened_str"],
        "closed_str":      datetime.now().strftime("%H:%M:%S %d/%m"),
        "timestamp":       time.time(),
        "entry_context":   entry_context,
        "exit_context":    exit_context,
    }
    _history.append(trade)
    _save_positions()
    _save_history()

    # Drift log en modo SIM — baseline para comparar contra live real
    try:
        entry_count = 0
        with open(DRIFT_LOG_FILE, "a") as _df:
            _df.write(json.dumps({
                "timestamp":             time.time(),
                "time_str":              datetime.now().strftime("%H:%M:%S %d/%m"),
                "mode":                  "sim",
                "symbol":                symbol,
                "wallet_label":          label,
                "program":               pos.get("entry_context", {}).get("program", ""),
                "sol_spent_real_sol":    round(amount_usd / _get_sol_price_usd(), 6),
                "sol_received_real_sol": round((amount_usd + pnl_usd) / _get_sol_price_usd(), 6),
                "real_pnl_sol":          round(pnl_usd / _get_sol_price_usd(), 6),
                "real_pnl_pct":          round(pnl_pct, 2),
                "hold_min":              round(hold_min, 1),
                "buy_latency_ms":        1500.0,
            }) + "\n")
        if os.path.exists(DRIFT_LOG_FILE):
            entry_count = sum(1 for _ in open(DRIFT_LOG_FILE))
        log.info(f"[DRIFT] SIM grabado #{entry_count} | {label} {symbol} | P&L: {round(pnl_pct,1)}% | hold: {round(hold_min,1)}min")
    except Exception as _e:
        log.warning(f"[DRIFT] Error grabando SIM: {_e}")

    # Actualizar reglas del learner
    try:
        from copytrade import learner
        learner.update()
    except Exception as e:
        log.debug(f"[LEARNER] Error actualizando reglas: {e}")

    icon  = "[bold green]✅ WIN [/]" if won else "[bold red]❌ LOSS[/]"
    color = "green" if won else "red"
    bal_color = "green" if _sim_balance >= SIM_INITIAL_CAPITAL else "red"

    total_cost_pct = (slippage_entry + slippage_exit + market_impact) * 100 + (fee_usd / amount_usd * 100)
    impact_str = f" + impact {market_impact*100:.1f}%" if market_impact > 0 else ""
    log.info(
        f"[SIM] {icon} | [cyan]{label}[/] vendió [yellow]{symbol}[/] | "
        f"[{color}]{pnl_pct:+.1f}%[/] ([{color}]${pnl_usd:+.2f}[/]) | "
        f"entrada ${entry:.8f} → salida ${price_exit:.8f} | "
        f"coste real: [dim]slip {slippage_entry*100:.1f}% + {slippage_exit*100:.1f}%{impact_str} + fee ${fee_usd:.3f} = {total_cost_pct:.1f}%[/] | "
        f"{hold_min:.0f} min | balance: [{bal_color}]${_sim_balance:.2f}[/]"
    )

    _print_summary()


# ── Resumen ───────────────────────────────────────────────────────────────────

def _print_summary():
    if not _history:
        return

    wins      = [t for t in _history if t["won"]]
    losses    = [t for t in _history if not t["won"]]
    pnl_total = _sim_balance - SIM_INITIAL_CAPITAL
    win_rate  = len(wins) / len(_history) * 100
    roi_pct   = pnl_total / SIM_INITIAL_CAPITAL * 100

    log.info(
        f"[SIM] 📊 RESUMEN | "
        f"Trades: [white]{len(_history)}[/] | "
        f"Win rate: [{'green' if win_rate >= 50 else 'red'}]{win_rate:.0f}%[/] "
        f"([green]{len(wins)}W[/]/[red]{len(losses)}L[/]) | "
        f"Balance: [{'green' if _sim_balance >= SIM_INITIAL_CAPITAL else 'red'}]"
        f"${_sim_balance:.2f}[/] | "
        f"ROI: [{'green' if roi_pct >= 0 else 'red'}]{roi_pct:+.1f}%[/]"
    )

    # Estadísticas del scorer cada 50 trades
    if _USE_SCORER and len(_history) % 50 == 0:
        total_seen = _scorer_accepted + _scorer_rejected
        if total_seen > 0:
            filter_pct = _scorer_rejected / total_seen * 100
            log.info(
                f"[SIM] 🤖 SCORER STATS | "
                f"Vistos: [white]{total_seen}[/] | "
                f"Aceptados: [green]{_scorer_accepted}[/] | "
                f"Rechazados: [red]{_scorer_rejected}[/] | "
                f"Filtro: [yellow]{filter_pct:.0f}%[/] de señales bloqueadas"
            )

    # Métricas avanzadas cada 50 trades
    if SIM_EXTENDED_METRICS and len(_history) % 50 == 0:
        metrics = get_advanced_metrics()
        if metrics and "profit_factor" in metrics:
            pf = metrics["profit_factor"]
            exp = metrics["expectancy"]
            dd = metrics["max_drawdown_pct"]
            avg_w = metrics["avg_winner_usd"]
            avg_l = metrics["avg_loser_usd"]
            sr = metrics.get("sharpe_ratio")

            pf_color = "green" if pf >= 1.3 else "yellow" if pf >= 1.0 else "red"
            exp_color = "green" if exp > 0 else "red"
            dd_color = "green" if dd <= 25 else "yellow" if dd <= 40 else "red"

            sr_str = f" | Sharpe: [white]{sr:.2f}[/]" if sr else ""
            log.info(
                f"[SIM] 🔥 MÉTRICAS AVANZADAS | "
                f"Profit Factor: [{pf_color}]{pf:.2f}x[/] (objetivo >1.3) | "
                f"Expectancy: [{exp_color}]${exp:+.2f}[/]/trade | "
                f"Max DD: [{dd_color}]{dd:+.1f}%[/] | "
                f"Avg W/L: ${avg_w:.2f}/${abs(avg_l):.2f}{sr_str}"
            )


def get_summary() -> dict:
    wins      = [t for t in _history if t["won"]]
    pnl_total = _sim_balance - SIM_INITIAL_CAPITAL
    open_pos  = len(_positions)
    return {
        "total_trades":    len(_history),
        "wins":            len(wins),
        "losses":          len(_history) - len(wins),
        "win_rate":        len(wins) / len(_history) * 100 if _history else 0,
        "balance":         round(_sim_balance, 2),
        "initial_capital": SIM_INITIAL_CAPITAL,
        "pnl_total_usd":   round(pnl_total, 2),
        "roi_pct":         round(pnl_total / SIM_INITIAL_CAPITAL * 100, 1) if SIM_INITIAL_CAPITAL else 0,
        "open_positions":  open_pos,
        "history":         _history[-20:],
    }


def get_advanced_metrics() -> dict:
    """Métricas profesionales de trading cuantitativo."""
    if not _history:
        return {}

    wins = [t for t in _history if t["won"]]
    losses = [t for t in _history if not t["won"]]

    if not wins or not losses:
        return {"note": "no hay suficientes trades"}

    # Profit factor: métrica REINA
    gross_profit = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    # Promedio de ganador/perdedor
    avg_winner = gross_profit / len(wins) if wins else 0
    avg_loser = gross_loss / len(losses) if losses else 0

    # Expectancy: el verdadero edge
    win_rate = len(wins) / len(_history)
    loss_rate = 1 - win_rate
    expectancy = (win_rate * avg_winner) - (loss_rate * avg_loser)

    # Max drawdown (peor caída equity)
    equity_curve = [SIM_INITIAL_CAPITAL]
    for trade in _history:
        equity_curve.append(trade["balance_after"])

    peak = equity_curve[0]
    max_dd_usd = 0
    max_dd_pct = 0
    for equity in equity_curve:
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd_usd:
            max_dd_usd = dd
            max_dd_pct = (dd / peak * 100) if peak > 0 else 0

    # Sharpe ratio aproximado (si hay >10 trades)
    if len(_history) > 10:
        pnl_list = [t["pnl_pct"] for t in _history]
        mean_pnl = sum(pnl_list) / len(pnl_list)
        variance = sum((x - mean_pnl) ** 2 for x in pnl_list) / len(pnl_list)
        std_pnl = math.sqrt(variance) if variance > 0 else 1.0
        sharpe_approx = mean_pnl / std_pnl if std_pnl > 0 else 0
    else:
        sharpe_approx = None

    return {
        "profit_factor": round(profit_factor, 2),
        "gross_profit_usd": round(gross_profit, 2),
        "gross_loss_usd": round(gross_loss, 2),
        "avg_winner_usd": round(avg_winner, 2),
        "avg_loser_usd": round(avg_loser, 2),
        "win_loss_ratio": round(avg_winner / abs(avg_loser), 2) if avg_loser != 0 else 0,
        "expectancy": round(expectancy, 2),
        "max_drawdown_usd": round(max_dd_usd, 2),
        "max_drawdown_pct": round(max_dd_pct, 1),
        "sharpe_ratio": round(sharpe_approx, 2) if sharpe_approx else None,
    }

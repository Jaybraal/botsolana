"""
Ejecuta el copy trade usando Jupiter API.
Modo proporcional: invierte el mismo % del capital que la wallet objetivo.
"""

import asyncio
import base64
import json
import os
import time
import httpx
from datetime import datetime
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.message import MessageV0, Message
from solana.rpc.api import Client
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TokenAccountOpts, TxOpts

from config import (
    RPC_HTTP, WALLET_PUBKEY, WALLET_PRIVKEY, SLIPPAGE_BPS,
    PROPORTIONAL_MODE, MAX_TRADE_PCT, MIN_TRADE_SOL, MAX_OPEN_COPIES,
    STOP_LOSS_PCT, MIN_RESERVE_SOL, MAX_PRICE_IMPACT, MAX_SESSION_LOSS_PCT, SCALING_TIERS,
    TOKENS, get_max_trade_pct_by_balance,
)
from utils.jupiter import get_quote, get_swap_transaction, calc_price_impact, out_amount
from utils.jupiter import get_quote_async, get_swap_transaction_async
from utils.pumpfun import get_pump_buy_tx, get_pump_sell_tx
from utils.pumpfun import get_pump_buy_tx_async, get_pump_sell_tx_async
from utils.logger import get_logger
from copytrade import simulator

log          = get_logger("executor")
client       = Client(RPC_HTTP)        # síncrono — solo para recover_open_positions al arrancar
_async_rpc   = AsyncClient(RPC_HTTP)   # async — hot path de trading
_async_http  = httpx.AsyncClient(timeout=5)  # para CoinGecko y otras llamadas HTTP async

os.makedirs("data", exist_ok=True)
COPYTRADES_FILE  = "data/copytrades.jsonl"
DEAD_TOKENS_FILE = "data/dead_tokens.json"
DRIFT_LOG_FILE   = "data/execution_drift.jsonl"

SOL_MINT     = TOKENS["SOL"]
LAMPORTS_PER_SOL = 1_000_000_000

# Tracking en memoria de posiciones abiertas: {token_mint: {"symbol": str, "opened": float}}
_open_copies: dict[str, dict] = {}

# Contador de intentos fallidos de compra por token — protección contra fees repetidas
_failed_buy_attempts: dict[str, int] = {}

# Circuit breaker de seguridad — detiene todos los trades si se pierde demasiado en la sesión
_circuit_breaker_triggered: bool = False
_initial_live_balance: int | None = None  # lamports al primer trade exitoso en LIVE_MODE

# Cooldown: {token: timestamp_de_ultima_venta} — evita reabrir tokens vendidos hace <2 min
_recent_sells: dict[str, float] = {}

# Balance inicial de SOL (lamports) — se registra en el primer trade en vivo
_initial_balance: int = 0

# Tokens confirmados como irrecuperables (rugged, sin liquidez) — persistido en disco
_dead_tokens: set[str] = set()

# Caché de precio SOL en USD — se refresca cada 60s (igual que simulator.py)
_sol_price_cache: float = 0.0
_sol_price_cache_ts: float = 0.0

# Caché de balance SOL propio — se refresca cada 5s (rara vez cambia más rápido)
_sol_balance_cache: int = 0
_sol_balance_cache_ts: float = 0.0

# Caché de keypair — decodificado una sola vez al inicio
_keypair_cache: "Keypair | None" = None

# Caché de blockhash — válido ~90s, refrescado en background cada 45s
_blockhash_cache: str | None = None
_blockhash_cache_ts: float = 0.0


def _load_dead_tokens():
    global _dead_tokens
    if os.path.exists(DEAD_TOKENS_FILE):
        try:
            with open(DEAD_TOKENS_FILE) as f:
                _dead_tokens = set(json.load(f))
        except Exception:
            _dead_tokens = set()

def _save_dead_token(mint: str, symbol: str):
    _dead_tokens.add(mint)
    try:
        with open(DEAD_TOKENS_FILE, "w") as f:
            json.dump(list(_dead_tokens), f)
        log.warning(f"[DEAD] {symbol} ({mint[:8]}...) marcado como irrecuperable — se ignorará en próximos reinicios")
    except Exception:
        pass

def _active_positions_count() -> int:
    """Cuenta solo posiciones abiertas activas (excluye recuperadas pendientes de señal)."""
    return sum(1 for v in _open_copies.values() if not v.get("recovered"))

_load_dead_tokens()


def recover_open_positions():
    """
    Al arrancar, escanea la wallet para detectar tokens no-SOL que ya tengamos.
    Intenta venderlos inmediatamente — si las wallets ya salieron, no llegará señal natural.
    Tokens que fallan TODAS las rutas de venta se marcan como irrecuperables en disco
    y se ignorarán en futuros reinicios (evita bloquear slots indefinidamente).
    """
    if not WALLET_PUBKEY:
        return
    keypair = load_keypair()
    try:
        from solana.rpc.types import TokenAccountOpts
        opts = TokenAccountOpts(program_id=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"))
        resp = client.get_token_accounts_by_owner_json_parsed(Pubkey.from_string(WALLET_PUBKEY), opts)
        DUST_THRESHOLD = 1_000_000
        recovered = 0
        for acc in resp.value:
            info      = acc.account.data.parsed["info"]
            mint      = info["mint"]
            tok       = info["tokenAmount"]
            raw_amt   = int(tok["amount"])
            ui_amt    = float(tok.get("uiAmount") or 0)
            if raw_amt < DUST_THRESHOLD:
                continue
            symbol = mint[:6]

            # Token ya marcado como irrecuperable en sesiones anteriores — ignorar
            if mint in _dead_tokens:
                log.debug(f"[RECOVER] {symbol} ya marcado como irrecuperable — ignorando")
                continue

            log.info(f"[RECOVER] Token encontrado: {symbol} ({mint[:8]}...) — {ui_amt:.4f} tokens — intentando vender...")

            # Intentar vender inmediatamente (wallets ya pudieron haber salido)
            sig = None
            if keypair:
                sig = _send_pumpfun_sell(mint, ui_amt, keypair, pool="pump")
                if not sig:
                    sig = _send_pumpfun_sell(mint, ui_amt, keypair, pool="pumpswap")
                if not sig:
                    sig = _send_swap(mint, SOL_MINT, raw_amt, keypair)

            if sig:
                log.info(f"[RECOVER] ✅ Vendido {symbol} al arrancar | TX: {sig[:20]}...")
            else:
                # Todas las rutas fallaron — muy probablemente token rugged/sin liquidez
                # Marcarlo como muerto para no volver a intentar en próximos reinicios
                _save_dead_token(mint, symbol)
                # Registrarlo en _open_copies como recuperado (sin contar en slots activos)
                # por si acaso llegara señal de venta natural
                _open_copies[mint] = {"symbol": symbol, "opened": time.time(), "recovered": True}
                log.warning(f"[RECOVER] ⚠️  No se pudo vender {symbol} — marcado como irrecuperable")
                recovered += 1

        if recovered:
            log.info(f"[RECOVER] {recovered} token(s) irrecuperables — NO bloquean slots de trading")
    except Exception as e:
        log.warning(f"[RECOVER] Error escaneando posiciones: {e}")


def _ensure_initial_balance():
    """Registra el balance inicial una sola vez, al primer trade en vivo."""
    global _initial_balance
    if _initial_balance == 0 and WALLET_PUBKEY:
        bal = get_our_sol_balance()
        if bal > 0:
            _initial_balance = bal
            log.info(
                f"[CAPITAL] Balance inicial registrado: "
                f"[bold white]{bal / LAMPORTS_PER_SOL:.4f} SOL[/] | "
                f"Stop-loss activo si cae bajo "
                f"[bold red]{bal * STOP_LOSS_PCT / LAMPORTS_PER_SOL:.4f} SOL[/] "
                f"({STOP_LOSS_PCT*100:.0f}%)"
            )


def _is_stop_loss_triggered(current_balance: int) -> bool:
    """Retorna True si el balance cayó por debajo del umbral de stop-loss."""
    if _initial_balance == 0:
        return False
    threshold = int(_initial_balance * STOP_LOSS_PCT)
    if current_balance < threshold:
        log.warning(
            f"[bold red][STOP-LOSS ACTIVO][/] Balance "
            f"{current_balance / LAMPORTS_PER_SOL:.4f} SOL < "
            f"umbral {threshold / LAMPORTS_PER_SOL:.4f} SOL — "
            f"trading pausado para proteger capital"
        )
        return True
    return False


def _get_sol_price_usd() -> float:
    """Precio SOL en USD con caché de 60s (versión sync para compatibilidad)."""
    return _sol_price_cache if _sol_price_cache > 0 else 150.0


async def _get_sol_price_usd_async() -> float:
    """Precio SOL en USD con caché de 60s — async, no bloquea el event loop."""
    global _sol_price_cache, _sol_price_cache_ts
    if time.time() - _sol_price_cache_ts < 60 and _sol_price_cache > 0:
        return _sol_price_cache
    try:
        resp = await _async_http.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
        )
        if resp.status_code == 200:
            price = float(resp.json().get("solana", {}).get("usd", 0))
            if price > 0:
                _sol_price_cache = price
                _sol_price_cache_ts = time.time()
    except Exception:
        pass
    return _sol_price_cache if _sol_price_cache > 0 else 150.0

def _get_dynamic_trade_pct(current_balance: int) -> float:
    """
    Retorna el % máximo por trade según el balance actual en USD.
    Tabla de riesgo dinámico según balance:
    - $50–$200: 25%
    - $200–$1k: 12%
    - $1k–$5k: 7%
    - $5k+: 3%
    """
    sol_price_usd = _get_sol_price_usd()
    balance_sol = current_balance / LAMPORTS_PER_SOL
    balance_usd = balance_sol * sol_price_usd
    trade_pct = get_max_trade_pct_by_balance(balance_usd)
    return trade_pct


# ── Drift log ────────────────────────────────────────────────────────────────

def _append_drift_log(entry: dict):
    """Persiste cada trade cerrado con métricas de ejecución real vs simulada."""
    with open(DRIFT_LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _log_drift_summary(entry: dict):
    """Imprime resumen de drift al cerrar un trade."""
    sym        = entry["symbol"]
    wallet     = entry["wallet_label"]
    spent      = entry["sol_spent_real_sol"]
    received   = entry["sol_received_real_sol"]
    pnl_sol    = entry["real_pnl_sol"]
    pnl_pct    = entry["real_pnl_pct"]
    hold_min   = entry["hold_min"]
    latency_ms = entry.get("buy_latency_ms", 0)

    color = "bold green" if pnl_sol >= 0 else "bold red"
    sign  = "+" if pnl_sol >= 0 else ""
    log.info(
        f"[DRIFT] [{color}]{wallet} {sym}[/] | "
        f"gastado: {spent:.5f} SOL → recibido: {received:.5f} SOL | "
        f"P&L real: [{color}]{sign}{pnl_sol:.5f} SOL ({sign}{pnl_pct:.1f}%)[/] | "
        f"hold: {hold_min:.1f}min | latencia buy: {latency_ms:.0f}ms"
    )


# ── Persistencia ─────────────────────────────────────────────────────────────

def _append_copytrade(entry: dict):
    with open(COPYTRADES_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Wallet / balance ──────────────────────────────────────────────────────────

def load_keypair() -> Keypair | None:
    global _keypair_cache
    if _keypair_cache is not None:
        return _keypair_cache
    if not WALLET_PRIVKEY:
        return None
    try:
        import base58
        _keypair_cache = Keypair.from_bytes(base58.b58decode(WALLET_PRIVKEY))
        return _keypair_cache
    except Exception as e:
        log.error(f"Error cargando keypair: {e}")
        return None


def get_our_sol_balance() -> int:
    """Balance SOL sync — solo para recover_open_positions al arrancar."""
    if not WALLET_PUBKEY:
        return 0
    try:
        resp = client.get_balance(Pubkey.from_string(WALLET_PUBKEY))
        return resp.value
    except Exception as e:
        log.error(f"Error obteniendo balance SOL: {e}")
        return 0


async def _get_sol_balance_async() -> int:
    """Balance SOL async con caché 5s — hot path de trading."""
    global _sol_balance_cache, _sol_balance_cache_ts
    if time.time() - _sol_balance_cache_ts < 5 and _sol_balance_cache > 0:
        return _sol_balance_cache
    if not WALLET_PUBKEY:
        return 0
    try:
        resp = await _async_rpc.get_balance(Pubkey.from_string(WALLET_PUBKEY))
        _sol_balance_cache = resp.value
        _sol_balance_cache_ts = time.time()
        return _sol_balance_cache
    except Exception as e:
        log.error(f"Error obteniendo balance SOL: {e}")
        return _sol_balance_cache if _sol_balance_cache > 0 else 0


def get_our_token_balance(mint: str) -> tuple[int, float]:
    """Balance de token sync — solo para recover_open_positions."""
    if not WALLET_PUBKEY:
        return 0, 0.0
    try:
        opts = TokenAccountOpts(mint=Pubkey.from_string(mint))
        resp = client.get_token_accounts_by_owner_json_parsed(Pubkey.from_string(WALLET_PUBKEY), opts)
        accounts = resp.value
        if not accounts:
            return 0, 0.0
        raw_total = 0
        ui_total  = 0.0
        for acc in accounts:
            info = acc.account.data.parsed["info"]["tokenAmount"]
            raw_total += int(info["amount"])
            ui_total  += float(info.get("uiAmount") or 0)
        return raw_total, ui_total
    except Exception as e:
        log.error(f"Error obteniendo balance token {mint[:8]}...: {e}")
        return 0, 0.0


async def _get_token_balance_async(mint: str) -> tuple[int, float]:
    """Balance de token async — hot path (venta)."""
    if not WALLET_PUBKEY:
        return 0, 0.0
    try:
        opts = TokenAccountOpts(mint=Pubkey.from_string(mint))
        resp = await _async_rpc.get_token_accounts_by_owner_json_parsed(
            Pubkey.from_string(WALLET_PUBKEY), opts
        )
        accounts = resp.value
        if not accounts:
            return 0, 0.0
        raw_total = 0
        ui_total  = 0.0
        for acc in accounts:
            info = acc.account.data.parsed["info"]["tokenAmount"]
            raw_total += int(info["amount"])
            ui_total  += float(info.get("uiAmount") or 0)
        return raw_total, ui_total
    except Exception as e:
        log.error(f"Error obteniendo balance token {mint[:8]}...: {e}")
        return 0, 0.0


# ── Cálculo de monto proporcional ────────────────────────────────────────────

async def calc_proportional_amount_async(swap: dict, our_balance_lamports: int) -> int | None:
    """Calcula lamports a invertir usando % dinámico del balance. Async para obtener precio SOL."""
    sol_price = await _get_sol_price_usd_async()
    balance_sol = our_balance_lamports / LAMPORTS_PER_SOL
    balance_usd = balance_sol * sol_price
    dynamic_pct = get_max_trade_pct_by_balance(balance_usd)
    our_amount  = int(our_balance_lamports * dynamic_pct)

    min_lamports = int(MIN_TRADE_SOL * LAMPORTS_PER_SOL)
    if our_amount < min_lamports:
        log.warning(
            f"Monto calculado ({our_amount / LAMPORTS_PER_SOL:.6f} SOL) "
            f"por debajo del mínimo ({MIN_TRADE_SOL} SOL) — ignorando"
        )
        return None

    return our_amount


# ── Execute ───────────────────────────────────────────────────────────────────

async def execute_copy(swap: dict) -> bool:
    """
    Ejecuta un copy del swap detectado en modo proporcional. Completamente async.

    - COMPRA (SOL→token): proporcional al % que metió la wallet.
    - VENTA  (token→SOL): vende todo el balance que tengamos de ese token.
    """
    keypair = load_keypair()
    label   = swap.get("wallet_label", f"{swap['wallet'][:8]}...")
    is_buy  = swap["token_in"] == SOL_MINT
    is_sell = swap["token_out"] == SOL_MINT

    # ── Modo simulación ──────────────────────────────────────────────────────
    if not keypair:
        _simulate(swap, label, is_buy)
        return True

    if not WALLET_PUBKEY:
        log.warning("WALLET_PUBKEY no configurado.")
        return False

    # ── Compra ───────────────────────────────────────────────────────────────
    if is_buy:
        global _circuit_breaker_triggered, _initial_live_balance

        # SEGURIDAD: Circuit breaker — detener si la sesión ya perdió demasiado
        if _circuit_breaker_triggered:
            log.warning("[SEGURIDAD] Circuit breaker activo — trading detenido. Reinicia el bot para continuar.")
            return False

        token_out = swap["token_out"]

        # No abrir más posiciones si ya estamos al límite (tokens recuperados no cuentan)
        active_count = _active_positions_count()
        if active_count >= MAX_OPEN_COPIES:
            log.warning(
                f"[{label}] Límite de {MAX_OPEN_COPIES} posiciones activas alcanzado — "
                f"ignorando compra de {swap['symbol_out']}"
            )
            return False

        # No comprar el mismo token dos veces
        if token_out in _open_copies:
            log.warning(f"[{label}] Ya tenemos {swap['symbol_out']} abierto — ignorando entrada adicional")
            return False

        # PROTECCIÓN 1: No reintentar tokens que fallaron 2+ veces (evita gastar fees repetidamente)
        if _failed_buy_attempts.get(token_out, 0) >= 2:
            log.debug(f"[{label}] {swap['symbol_out']} ya falló 2 veces — ignorando para ahorrar fees")
            return False

        # PROTECCIÓN 4: Cooldown de 2 min — evita reabrir tokens que acaban de venderse
        # Si fue vendido hace <2 min, significa que el trade fue muy corto y probablemente pérdida
        last_sell_time = _recent_sells.get(token_out, 0)
        if last_sell_time and (time.time() - last_sell_time) < 120:  # 2 minutos
            log.debug(f"[{label}] {swap['symbol_out']} vendido hace {time.time() - last_sell_time:.0f}s — cooldown activo")
            return False

        _swap_program = swap.get("program", "")

        # FAST COPY: trades de PumpPortal WS de wallets objetivo — skip DexScreener y scorer.
        # El razonamiento: si una wallet top (Theo, Cupsey-2, etc.) compra algo en Pump.fun,
        # la señal ya fue validada por la wallet. Cada ms de latencia añadida aquí = peor precio.
        # Variable FAST_COPY_PUMPPORTAL (default=true) controla este comportamiento.
        _fast_copy = (
            swap.get("source") == "pumpportal"
            and os.getenv("FAST_COPY_PUMPPORTAL", "true").lower() == "true"
        )

        _pair_info = None
        _liquidity_usd = 0.0

        if not _fast_copy:
            # PROTECCIÓN 3: Verificar liquidez mínima en DexScreener (solo en modo normal)
            _min_liquidity = float(os.getenv("MIN_LIQUIDITY_USD", "500"))
            from utils.dexscreener import get_best_pair_async
            _pair_info = await get_best_pair_async(token_out)
            _liquidity_usd = float((_pair_info or {}).get("liquidity", {}).get("usd", 0))
            if _pair_info and _liquidity_usd < _min_liquidity:
                log.warning(
                    f"[{label}] Liquidez ${_liquidity_usd:.0f} < ${_min_liquidity:.0f} — "
                    f"abortando para evitar slippage extremo"
                )
                return False

            # SCORER: Evaluar token contra patrones Groq aprendidos de historial
            _use_scorer = os.getenv("USE_GROQ_SCORER", "true").lower() == "true"
            if _use_scorer:
                from copytrade.scorer import should_copy
                _pair_created_ms = (_pair_info or {}).get("pairCreatedAt") or 0
                _pair_created_s  = _pair_created_ms // 1000 if _pair_created_ms > 1e10 else _pair_created_ms
                _token_age_min   = round((time.time() - _pair_created_s) / 60, 1) if _pair_created_s else None
                _token_info = {
                    "program":         _swap_program,
                    "liquidity_usd":   _liquidity_usd,
                    "token_age_min":   _token_age_min,
                    "mcap_usd":        float((_pair_info or {}).get("marketCap") or (_pair_info or {}).get("fdv") or 0),
                    "price_change_5m": float(((_pair_info or {}).get("priceChange") or {}).get("m5") or 0),
                    "price_change_1h": float(((_pair_info or {}).get("priceChange") or {}).get("h1") or 0),
                    "buys_5m":         int((((_pair_info or {}).get("txns") or {}).get("m5") or {}).get("buys") or 0),
                    "sells_5m":        int((((_pair_info or {}).get("txns") or {}).get("m5") or {}).get("sells") or 0),
                }
                _score_pass, _score_reason = should_copy(label, _token_info)
                if not _score_pass:
                    log.info(f"[{label}] ❌ Scorer rechazó {swap['symbol_out']} — {_score_reason}")
                    return False
            else:
                # Fallback: filtro AMM clásico cuando scorer está desactivado
                _only_amm = os.getenv("ONLY_AMM_SWAPS", "false").lower() == "true"
                if _only_amm and _swap_program == "Pump.fun":
                    log.info(f"[{label}] Ignorando {swap['symbol_out']} en Pump.fun BC (AMM filter)")
                    return False
        else:
            log.info(f"[{label}] ⚡ FAST COPY {swap['symbol_out']} — skip DexScreener/scorer")

        our_balance = await _get_sol_balance_async()
        if our_balance == 0:
            log.error("No se pudo obtener balance SOL propio.")
            return False

        # Registrar capital inicial (sólo la primera vez)
        _ensure_initial_balance()

        # Circuit breaker de sesión: inicializar balance al primer trade en LIVE_MODE
        if _initial_live_balance is None:
            _initial_live_balance = our_balance

        # Verificar pérdida máxima en la sesión actual — seguridad automática
        if _initial_live_balance > 0:
            session_loss = 1 - (our_balance / _initial_live_balance)
            if session_loss >= MAX_SESSION_LOSS_PCT:
                _circuit_breaker_triggered = True
                log.warning(
                    f"🚨 CIRCUIT BREAKER ACTIVADO — Pérdida de sesión: {session_loss*100:.1f}% "
                    f"(máx: {MAX_SESSION_LOSS_PCT*100:.0f}%) — TODOS LOS TRADES DETENIDOS"
                )
                return False

        # Stop-loss global: parar si perdimos demasiado
        if _is_stop_loss_triggered(our_balance):
            return False

        amount_lamports = await calc_proportional_amount_async(swap, our_balance)
        if amount_lamports is None:
            return False

        # Reserva mínima: nunca dejar el balance por debajo de MIN_RESERVE_SOL
        min_reserve_lamports = int(MIN_RESERVE_SOL * LAMPORTS_PER_SOL)
        available_lamports = our_balance - min_reserve_lamports
        if available_lamports <= 0:
            log.warning(
                f"[{label}] Balance ({our_balance / LAMPORTS_PER_SOL:.4f} SOL) "
                f"no supera la reserva mínima ({MIN_RESERVE_SOL} SOL) — ignorando"
            )
            return False
        if amount_lamports > available_lamports:
            log.info(
                f"[{label}] Monto reducido de {amount_lamports / LAMPORTS_PER_SOL:.4f} "
                f"a {available_lamports / LAMPORTS_PER_SOL:.4f} SOL para respetar reserva mínima"
            )
            amount_lamports = available_lamports

        proportion_pct = amount_lamports / our_balance * 100
        log.info(
            f"[COPY BUY] [bold cyan]{label}[/] | {swap['symbol_out']} | "
            f"Proporción: {proportion_pct:.1f}% | "
            f"Monto: {amount_lamports / LAMPORTS_PER_SOL:.4f} SOL | "
            f"Balance: {our_balance / LAMPORTS_PER_SOL:.3f} SOL"
        )

        # PROTECCIÓN 2: Pre-check de price impact ANTES de enviar TX (evita TX que van a fallar)
        # Skip en fast copy (token recién salido, Jupiter aún no lo conoce) y en Pump.fun BC.
        is_pumpfun_bc  = swap.get("program") == "Pump.fun"
        if not _fast_copy and not is_pumpfun_bc:
            _pre_quote = await get_quote_async(swap["token_in"], token_out, amount_lamports)
            if _pre_quote and calc_price_impact(_pre_quote) > MAX_PRICE_IMPACT:
                log.warning(
                    f"[{label}] Price impact {calc_price_impact(_pre_quote):.2f}% > {MAX_PRICE_IMPACT}% — "
                    f"abortando para evitar TX fallida con pérdida de fees"
                )
                return False
        is_pumpswap    = swap.get("program") == "PumpSwap"

        # DRIFT: balance justo antes de ejecutar y timestamp de inicio
        _bal_before_buy = await _get_sol_balance_async()
        _buy_started_at = time.time()

        sig = None
        if is_pumpfun_bc:
            log.info(f"[{label}] Bonding curve — usando PumpPortal async para {swap['symbol_out']}")
            sig = await _send_pumpfun_buy_async(token_out, amount_lamports, keypair)
        elif is_pumpswap:
            sig = await _send_swap_async(swap["token_in"], token_out, amount_lamports, keypair)
            if not sig:
                log.info(f"[{label}] Jupiter falló — intentando PumpPortal (pumpswap) para {swap['symbol_out']}")
                sig = await _send_pumpfun_buy_pumpswap_async(token_out, amount_lamports, keypair)
        else:
            sig = await _send_swap_async(swap["token_in"], token_out, amount_lamports, keypair)

        if not sig:
            log.warning(f"[{label}] No se pudo ejecutar buy — {swap['symbol_out']} (programa: {swap.get('program','')})")
            _failed_buy_attempts[token_out] = _failed_buy_attempts.get(token_out, 0) + 1
            return False

        # DRIFT: balance justo después — diferencia = SOL real gastado (incluye fees de red)
        _sol_balance_cache_ts = 0  # invalidar caché tras TX
        _bal_after_buy   = await _get_sol_balance_async()
        _sol_spent_real  = (_bal_before_buy - _bal_after_buy) / LAMPORTS_PER_SOL
        _buy_latency_ms  = (time.time() - _buy_started_at) * 1000

        _open_copies[token_out] = {
            "symbol":          swap["symbol_out"],
            "opened":          time.time(),
            "program":         swap.get("program", ""),
            # métricas drift
            "sol_spent_real":  _sol_spent_real,
            "buy_latency_ms":  _buy_latency_ms,
            "wallet_label":    label,
        }
        _append_copytrade({
            "timestamp":      time.time(),
            "time_str":       datetime.now().strftime("%H:%M:%S %d/%m"),
            "type":           "buy",
            "wallet":         swap["wallet"],
            "wallet_label":   label,
            "program":        swap["program"],
            "symbol_in":      swap["symbol_in"],
            "symbol_out":     swap["symbol_out"],
            "token_in":       swap["token_in"],
            "token_out":      token_out,
            "amount_sol":     amount_lamports / LAMPORTS_PER_SOL,
            "sol_spent_real": _sol_spent_real,
            "buy_latency_ms": round(_buy_latency_ms, 1),
            "proportion_pct": round(proportion_pct, 2),
            "tx_sig":         sig,
            "simulated":      False,
        })
        log.info(f"[bold green]COPY BUY OK[/] — {swap['symbol_out']} | TX: {sig}")
        # Alimentar el simulador en vivo para acumular datos de aprendizaje
        simulator.process(swap)
        return True

    # ── Venta ────────────────────────────────────────────────────────────────
    elif is_sell:
        token_in = swap["token_in"]

        # Solo vendemos si tenemos ese token en posición abierta
        if token_in not in _open_copies:
            log.debug(f"[{label}] Venta de {swap['symbol_in']} ignorada — no tenemos posición abierta")
            return False

        raw_balance, ui_balance = await _get_token_balance_async(token_in)
        if raw_balance == 0:
            # El nodo RPC puede tardar varios segundos en reflejar una cuenta recién creada.
            # Reintentar hasta 5 veces con 3s de pausa async si la posición lleva < 60s abierta.
            opened_at = _open_copies[token_in].get("opened", 0)
            if time.time() - opened_at < 60:
                for _attempt in range(5):
                    await asyncio.sleep(3)
                    raw_balance, ui_balance = await _get_token_balance_async(token_in)
                    if raw_balance > 0:
                        log.info(f"[{label}] Balance visible tras {(_attempt+1)*3}s de espera — {ui_balance:.4f} tokens")
                        break
        if raw_balance == 0:
            log.warning(f"[{label}] Venta de {swap['symbol_in']} — balance propio es 0, nada que vender")
            _open_copies.pop(token_in, None)
            return False

        log.info(
            f"[COPY SELL] [bold cyan]{label}[/] | {swap['symbol_in']}→SOL | "
            f"Vendiendo todo: {ui_balance:.4f} tokens ({raw_balance} raw)"
        )

        # Usar el programa con el que NOSOTROS compramos (no el del target al vender)
        # para decidir la ruta de venta más adecuada.
        buy_program   = _open_copies[token_in].get("program", swap.get("program", ""))
        is_pumpfun_bc = buy_program == "Pump.fun"
        sig = None
        if is_pumpfun_bc:
            # Token de Pump.fun: probar BC → PumpSwap AMM → Jupiter (todo async)
            log.info(f"[{label}] Intentando PumpPortal async (pump) — {ui_balance:.4f} tokens")
            sig = await _send_pumpfun_sell_async(token_in, ui_balance, keypair, pool="pump")
            if not sig:
                log.info(f"[{label}] Intentando PumpPortal async (pumpswap) — token graduado?")
                sig = await _send_pumpfun_sell_async(token_in, ui_balance, keypair, pool="pumpswap")
            if not sig:
                log.info(f"[{label}] Intentando Jupiter async como último recurso...")
                sig = await _send_swap_async(token_in, SOL_MINT, raw_balance, keypair)
        else:
            sig = await _send_swap_async(token_in, SOL_MINT, raw_balance, keypair)
            if not sig:
                log.info(f"[{label}] Jupiter falló — intentando PumpPortal async (pumpswap)...")
                sig = await _send_pumpfun_sell_async(token_in, ui_balance, keypair, pool="pumpswap")

        if not sig:
            log.warning(f"[{label}] Sell falló (pump + pumpswap + Jupiter) — {swap['symbol_in']} — posición queda abierta")
            return False

        # DRIFT: balance antes de la venta (invalidar caché para leer valor real)
        _sol_balance_cache_ts = 0
        _bal_before_sell = await _get_sol_balance_async()

        pos = _open_copies.pop(token_in, {})
        _failed_buy_attempts.pop(token_in, None)
        _recent_sells[token_in] = time.time()
        hold_min = (time.time() - pos.get("opened", time.time())) / 60

        # DRIFT: balance después — diferencia = SOL real recibido (neto de fees)
        _bal_after_sell      = await _get_sol_balance_async()
        _sol_received_real   = (_bal_after_sell - _bal_before_sell) / LAMPORTS_PER_SOL
        _sol_spent_real      = pos.get("sol_spent_real", 0.0)
        _real_pnl_sol        = _sol_received_real - _sol_spent_real
        _real_pnl_pct        = (_real_pnl_sol / _sol_spent_real * 100) if _sol_spent_real > 0 else 0.0

        drift_entry = {
            "timestamp":           time.time(),
            "time_str":            datetime.now().strftime("%H:%M:%S %d/%m"),
            "symbol":              swap["symbol_in"],
            "wallet_label":        pos.get("wallet_label", label),
            "program":             pos.get("program", swap.get("program", "")),
            "sol_spent_real_sol":  round(_sol_spent_real, 6),
            "sol_received_real_sol": round(_sol_received_real, 6),
            "real_pnl_sol":        round(_real_pnl_sol, 6),
            "real_pnl_pct":        round(_real_pnl_pct, 2),
            "hold_min":            round(hold_min, 1),
            "buy_latency_ms":      round(pos.get("buy_latency_ms", 0), 1),
            "tx_sig_sell":         sig,
        }
        _append_drift_log(drift_entry)
        _log_drift_summary(drift_entry)

        _append_copytrade({
            "timestamp":             time.time(),
            "time_str":              datetime.now().strftime("%H:%M:%S %d/%m"),
            "type":                  "sell",
            "wallet":                swap["wallet"],
            "wallet_label":          label,
            "program":               swap["program"],
            "symbol_in":             swap["symbol_in"],
            "symbol_out":            swap["symbol_out"],
            "token_in":              token_in,
            "token_out":             SOL_MINT,
            "hold_min":              round(hold_min, 1),
            "sol_received_real":     round(_sol_received_real, 6),
            "real_pnl_sol":          round(_real_pnl_sol, 6),
            "real_pnl_pct":          round(_real_pnl_pct, 2),
            "tx_sig":                sig,
            "simulated":             False,
        })
        log.info(f"[bold green]COPY SELL OK[/] — {swap['symbol_in']} | Hold: {hold_min:.1f} min | TX: {sig}")
        # Alimentar el simulador en vivo para acumular datos de aprendizaje
        simulator.process(swap)
        return True

    else:
        # Token→token: ignorar, demasiado complejo de proporcionar sin precio
        log.debug(f"[{label}] Swap token→token ignorado ({swap['symbol_in']}→{swap['symbol_out']})")
        return False


# ── Enviar TX async ────────────────────────────────────────────────────────────

async def _send_swap_async(token_in: str, token_out: str, amount: int, keypair: Keypair) -> str | None:
    """Jupiter quote + swap TX completamente async. Sin bloquear el event loop."""
    quote = await get_quote_async(token_in, token_out, amount)
    if not quote:
        log.error("No se pudo obtener quote de Jupiter (async).")
        return None

    impact = calc_price_impact(quote)
    if impact > MAX_PRICE_IMPACT:
        log.warning(f"Price impact muy alto ({impact:.2f}% > {MAX_PRICE_IMPACT}%) — abortando.")
        return None

    swap_tx_b64 = await get_swap_transaction_async(quote, WALLET_PUBKEY)
    if not swap_tx_b64:
        log.error("No se pudo obtener swap TX de Jupiter (async).")
        return None

    return await _sign_and_send_async(base64.b64decode(swap_tx_b64), keypair, f"Jupiter {token_out[:8]}")


async def _send_pumpfun_buy_async(mint: str, amount_lamports: int, keypair: Keypair) -> str | None:
    """Compra async en bonding curve via PumpPortal."""
    amount_sol = amount_lamports / LAMPORTS_PER_SOL
    tx_bytes = await get_pump_buy_tx_async(WALLET_PUBKEY, mint, amount_sol)
    if not tx_bytes:
        return None
    return await _sign_and_send_async(tx_bytes, keypair, f"PumpPortal buy {mint[:8]}")


async def _send_pumpfun_buy_pumpswap_async(mint: str, amount_lamports: int, keypair: Keypair) -> str | None:
    """Compra async en PumpSwap AMM."""
    from utils.pumpfun import _multi_backend_async
    amount_sol = amount_lamports / LAMPORTS_PER_SOL
    payload = {
        "publicKey": WALLET_PUBKEY, "action": "buy", "mint": mint,
        "denominatedInSol": "true", "amount": round(amount_sol, 6),
        "slippage": 15, "priorityFee": 0.0002, "pool": "pumpswap",
    }
    tx_bytes = await _multi_backend_async(payload, f"buy pumpswap {mint[:8]}")
    if not tx_bytes:
        return None
    return await _sign_and_send_async(tx_bytes, keypair, f"PumpSwap buy {mint[:8]}")


async def _send_pumpfun_sell_async(mint: str, ui_amount: float, keypair: Keypair, pool: str = "pump") -> str | None:
    """Venta async via PumpPortal."""
    tx_bytes = await get_pump_sell_tx_async(WALLET_PUBKEY, mint, ui_amount, pool=pool)
    if not tx_bytes:
        return None
    return await _sign_and_send_async(tx_bytes, keypair, f"PumpPortal sell [{pool}] {mint[:8]}")


async def _sign_and_send_async(tx_bytes: bytes, keypair: Keypair, desc: str) -> str | None:
    """Firma y envía TX usando RPC async. Retorna signature sin esperar confirmación."""
    global _blockhash_cache, _blockhash_cache_ts
    try:
        tx = VersionedTransaction.from_bytes(tx_bytes)

        # Usar blockhash cacheado si tiene <60s — evita RPC call en hot path
        if _blockhash_cache and (time.time() - _blockhash_cache_ts) < 60:
            fresh_bh = _blockhash_cache
        else:
            bh_resp = await _async_rpc.get_latest_blockhash(commitment="confirmed")
            _blockhash_cache = bh_resp.value.blockhash
            _blockhash_cache_ts = time.time()
            fresh_bh = _blockhash_cache

        msg = tx.message
        if isinstance(msg, MessageV0):
            new_msg = MessageV0(
                header=msg.header,
                account_keys=list(msg.account_keys),
                recent_blockhash=fresh_bh,
                instructions=list(msg.instructions),
                address_table_lookups=list(msg.address_table_lookups),
            )
        else:
            new_msg = Message.new_with_blockhash(msg.instructions, keypair.pubkey(), fresh_bh)

        tx_signed = VersionedTransaction(new_msg, [keypair])
        resp = await _async_rpc.send_raw_transaction(
            bytes(tx_signed),
            opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"),
        )
        # Retornar sig inmediatamente — no esperar confirmación (ahorra 3-30s)
        return str(resp.value)
    except Exception as e:
        log.error(f"[{desc}] Error firmando/enviando TX async: {e}")
        return None


async def _refresh_blockhash_loop():
    """Renueva el blockhash cacheado cada 45s en background — elimina RPC call del hot path."""
    global _blockhash_cache, _blockhash_cache_ts
    while True:
        try:
            bh_resp = await _async_rpc.get_latest_blockhash(commitment="confirmed")
            _blockhash_cache = bh_resp.value.blockhash
            _blockhash_cache_ts = time.time()
        except Exception as e:
            log.debug(f"[blockhash refresh] {e}")
        await asyncio.sleep(45)


async def _refresh_balance_loop():
    """Renueva el balance SOL cacheado cada 4s en background — siempre fresco en hot path."""
    global _sol_balance_cache, _sol_balance_cache_ts
    if not WALLET_PUBKEY:
        return
    while True:
        try:
            resp = await _async_rpc.get_balance(Pubkey.from_string(WALLET_PUBKEY))
            _sol_balance_cache = resp.value
            _sol_balance_cache_ts = time.time()
        except Exception as e:
            log.debug(f"[balance refresh] {e}")
        await asyncio.sleep(4)


# ── Envío sync (solo para recover_open_positions al arrancar) ─────────────────

def _send_swap(token_in: str, token_out: str, amount: int, keypair: Keypair) -> str | None:
    """Jupiter sync — solo usado en recover_open_positions al inicio."""
    quote = get_quote(token_in, token_out, amount)
    if not quote:
        return None
    impact = calc_price_impact(quote)
    if impact > MAX_PRICE_IMPACT:
        log.warning(f"Price impact muy alto ({impact:.2f}%) — abortando.")
        return None
    swap_tx_b64 = get_swap_transaction(quote, WALLET_PUBKEY)
    if not swap_tx_b64:
        return None
    try:
        raw_bytes = base64.b64decode(swap_tx_b64)
        tx        = VersionedTransaction.from_bytes(raw_bytes)
        tx_signed = VersionedTransaction(tx.message, [keypair])
        resp      = client.send_raw_transaction(
            bytes(tx_signed),
            opts=TxOpts(skip_preflight=False, preflight_commitment="confirmed"),
        )
        return str(resp.value)
    except Exception as e:
        log.error(f"Error enviando TX Jupiter sync: {e}")
        return None


# ── Funciones sync para recover_open_positions ────────────────────────────────

def _send_pumpfun_sell(mint: str, ui_amount: float, keypair: Keypair, pool: str = "pump") -> str | None:
    """Venta sync de PumpPortal — solo usada en recover_open_positions al arrancar."""
    tx_bytes = get_pump_sell_tx(WALLET_PUBKEY, mint, ui_amount, pool=pool)
    if not tx_bytes:
        return None
    try:
        tx        = VersionedTransaction.from_bytes(tx_bytes)
        bh_resp   = client.get_latest_blockhash(commitment="confirmed")
        fresh_bh  = bh_resp.value.blockhash
        msg = tx.message
        if isinstance(msg, MessageV0):
            new_msg = MessageV0(
                header=msg.header, account_keys=list(msg.account_keys),
                recent_blockhash=fresh_bh, instructions=list(msg.instructions),
                address_table_lookups=list(msg.address_table_lookups),
            )
        else:
            new_msg = Message.new_with_blockhash(msg.instructions, keypair.pubkey(), fresh_bh)
        tx_signed = VersionedTransaction(new_msg, [keypair])
        resp = client.send_raw_transaction(bytes(tx_signed), opts=TxOpts(skip_preflight=False, preflight_commitment="confirmed"))
        return str(resp.value)
    except Exception as e:
        log.error(f"[sync sell] Error: {e}")
        return None


# ── Simulación ────────────────────────────────────────────────────────────────

def _simulate(swap: dict, label: str, is_buy: bool):
    """Registra el swap en modo simulación."""
    direction = "COMPRA" if is_buy else "VENTA"
    entry = {
        "timestamp":    time.time(),
        "time_str":     datetime.now().strftime("%H:%M:%S %d/%m"),
        "type":         "buy" if is_buy else ("sell" if swap["token_out"] == SOL_MINT else "token-token"),
        "wallet":       swap["wallet"],
        "wallet_label": label,
        "program":      swap["program"],
        "symbol_in":    swap["symbol_in"],
        "symbol_out":   swap["symbol_out"],
        "token_in":     swap["token_in"],
        "token_out":    swap["token_out"],
        "amount_in":    swap["amount_in"],
        "simulated":    True,
    }
    _append_copytrade(entry)
    log.info(
        f"[SIM] [bold cyan]{label}[/] | {direction} | "
        f"[yellow]{swap['symbol_in']}[/] → [green]{swap['symbol_out']}[/] "
        f"via [white]{swap['program']}[/]"
    )
    simulator.process(swap)

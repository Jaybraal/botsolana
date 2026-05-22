"""
Scanner autónomo — opera sin copiar wallets.

Flujo:
  1. PumpPortal WS subscribeNewToken → detecta cada token nuevo en Pump.fun
  2. subscribeTokenTrade → acumula buys del token en tiempo real
  3. Trigger a los AUTO_EVAL_DELAY_MIN minutos (o antes si hay momentum alto)
  4. Fetch DexScreener → score con stat_scorer
  5. Si score >= SCORER_THRESHOLD → compra (execute_copy en SIM o LIVE)
  6. Monitor de precio cada 30s → aplica stop loss / take profit / trailing / timeout

Variables de entorno:
  AUTO_EVAL_DELAY_MIN   = 7     # minutos antes de evaluar un token nuevo
  AUTO_MOMENTUM_BUYS    = 150   # buys acumulados para evaluar antes del tiempo
  AUTO_STOP_LOSS_PCT    = -15   # % de caída → vender
  AUTO_TAKE_PROFIT_PCT  = 40    # % de ganancia → vender
  AUTO_TRAILING_PEAK    = 20    # % de ganancia mínima para activar trailing
  AUTO_TRAILING_DROP    = 10    # % de caída desde el pico → vender con trailing
  AUTO_MAX_HOLD_MIN     = 12    # minutos máximos antes de cerrar forzado
  AUTO_MAX_POSITIONS    = 3     # máximo de posiciones autónomas simultáneas
"""

import asyncio
import json
import os
import ssl
import time
import random

import certifi
import httpx
import websockets

from config import TOKENS
from copytrade.executor import execute_copy
from copytrade.stat_scorer import score_token
from utils.dexscreener import get_best_pair
from utils.logger import get_logger

log = get_logger("auto_scanner")

SOL_MINT       = TOKENS["SOL"]
PUMPPORTAL_WS  = "wss://pumpportal.fun/api/data"
PUMPPORTAL_API = "https://pumpportal.fun/api/coin-data"

# Caché de precio SOL en USD — se refresca cada 60s para no llamar CoinGecko en cada tick
_sol_price_usd: float = 150.0
_sol_price_ts:  float = 0.0

def _get_sol_price_usd() -> float:
    global _sol_price_usd, _sol_price_ts
    if time.time() - _sol_price_ts < 60:
        return _sol_price_usd
    try:
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
            timeout=3,
        )
        if r.status_code == 200:
            _sol_price_usd = float(r.json().get("solana", {}).get("usd", _sol_price_usd))
            _sol_price_ts = time.time()
    except Exception:
        pass
    return _sol_price_usd

# ── Config desde env ─────────────────────────────────────────────────────────
EVAL_DELAY_MIN   = float(os.getenv("AUTO_EVAL_DELAY_MIN",   "7"))
MOMENTUM_BUYS    = int(os.getenv("AUTO_MOMENTUM_BUYS",      "150"))
STOP_LOSS_PCT    = float(os.getenv("AUTO_STOP_LOSS_PCT",    "-15"))
TAKE_PROFIT_PCT  = float(os.getenv("AUTO_TAKE_PROFIT_PCT",  "40"))
TRAILING_PEAK    = float(os.getenv("AUTO_TRAILING_PEAK",    "20"))
TRAILING_DROP    = float(os.getenv("AUTO_TRAILING_DROP",    "10"))
MAX_HOLD_MIN     = float(os.getenv("AUTO_MAX_HOLD_MIN",     "12"))
MAX_POSITIONS    = int(os.getenv("AUTO_MAX_POSITIONS",      "3"))
MONITOR_INTERVAL = 10  # segundos entre checks de precio

# ── Estado en memoria ────────────────────────────────────────────────────────
# {mint: {created_at, buys, name, evaluated, symbol}}
_tracked: dict[str, dict] = {}

# {mint: {entry_price_usd, entry_time, peak_pct, symbol, program}}
_auto_positions: dict[str, dict] = {}

_lock = asyncio.Lock()


# ── Helpers DexScreener + PumpPortal ─────────────────────────────────────────

def _fetch_pumpportal_price(mint: str) -> dict | None:
    """
    Obtiene precio de un token directamente desde la bonding curve de PumpPortal.
    Usado como fallback cuando DexScreener aún no indexó el token (< ~15 min).
    El precio se calcula: virtualSolReserves / virtualTokenReserves
    """
    try:
        r = httpx.get(PUMPPORTAL_API, params={"mint": mint}, timeout=3)
        if r.status_code != 200:
            return None
        d = r.json()
        v_sol = float(d.get("virtual_sol_reserves") or 0)
        v_tok = float(d.get("virtual_token_reserves") or 0)
        if v_sol <= 0 or v_tok <= 0:
            return None
        # Pump.fun tokens: SOL reserves en lamports (1e9), token reserves en unidades mínimas (1e6)
        price_sol = (v_sol / 1e9) / (v_tok / 1e6)
        price_usd = price_sol * _get_sol_price_usd()
        mcap_usd  = float(d.get("market_cap") or d.get("usd_market_cap") or 0)
        return {
            "price_usd":     price_usd,
            "price_sol":     price_sol,
            "liquidity_usd": 0.0,   # bonding curve no tiene liquidez en el sentido de AMM
            "mcap_usd":      mcap_usd,
            "token_age_min": None,
            "program":       "Pump.fun",
            "buys_5m":       0,
            "sells_5m":      0,
            "price_change_1h": 0.0,
            "price_change_5m": 0.0,
            "pair_address":  "",
        }
    except Exception as e:
        log.debug(f"[auto] PumpPortal price error {mint[:8]}: {e}")
        return None


def _fetch_token_info(mint: str) -> dict | None:
    """
    Obtiene datos actuales del token. Intenta DexScreener primero.
    Si el token no está indexado (muy nuevo), usa PumpPortal API como fallback.
    """
    try:
        pair = get_best_pair(mint)
        if pair:
            liq  = float((pair.get("liquidity") or {}).get("usd") or 0)
            mcap = float(pair.get("marketCap") or pair.get("fdv") or 0)
            pc   = pair.get("priceChange") or {}
            txns = (pair.get("txns") or {}).get("m5") or {}
            created_ms = pair.get("pairCreatedAt") or 0
            created_s  = created_ms // 1000 if created_ms > 1e10 else created_ms
            age_min    = round((time.time() - created_s) / 60, 1) if created_s else None
            dex_label  = (pair.get("dexId") or "").lower()
            if "raydium" in dex_label:
                program = "Raydium"
            elif "pumpswap" in dex_label or "pump_amm" in dex_label:
                program = "PumpSwap"
            else:
                program = "Pump.fun"
            price_usd = float(pair.get("priceUsd") or 0)
            price_sol = float(pair.get("priceNative") or 0)
            # Si DexScreener tiene el par pero sin precio, intentar PumpPortal
            if price_usd <= 0:
                pp = _fetch_pumpportal_price(mint)
                if pp:
                    price_usd = pp["price_usd"]
                    price_sol = pp["price_sol"]
            return {
                "liquidity_usd":   liq,
                "mcap_usd":        mcap,
                "price_change_1h": float(pc.get("h1") or 0),
                "price_change_5m": float(pc.get("m5") or 0),
                "buys_5m":         int(txns.get("buys") or 0),
                "sells_5m":        int(txns.get("sells") or 0),
                "token_age_min":   age_min,
                "program":         program,
                "price_usd":       price_usd,
                "price_sol":       price_sol,
                "pair_address":    pair.get("pairAddress", ""),
            }
    except Exception as e:
        log.debug(f"[auto] DexScreener error {mint[:8]}: {e}")

    # Fallback: PumpPortal directo (token en bonding curve sin indexar aún)
    pp = _fetch_pumpportal_price(mint)
    if pp:
        log.debug(f"[auto] {mint[:8]}: usando precio PumpPortal (sin DexScreener aún)")
    return pp


# ── Monitor de precio por posición autónoma ───────────────────────────────────

async def _monitor_position(mint: str, symbol: str):
    """
    Monitorea precio de una posición abierta cada MONITOR_INTERVAL segundos.
    Aplica stop loss, take profit, trailing stop y timeout.
    """
    pos = _auto_positions.get(mint)
    if not pos:
        return

    entry_price = pos["entry_price_usd"]
    entry_time  = pos["entry_time"]
    program     = pos["program"]

    log.info(
        f"[auto] 👁 Monitor iniciado | {symbol} | entrada ${entry_price:.8f} | "
        f"SL {STOP_LOSS_PCT:+.0f}% | TP +{TAKE_PROFIT_PCT:.0f}% | "
        f"trailing >{TRAILING_PEAK:.0f}% cae -{TRAILING_DROP:.0f}% | "
        f"max {MAX_HOLD_MIN:.0f}min"
    )

    while mint in _auto_positions:
        await asyncio.sleep(MONITOR_INTERVAL)

        if mint not in _auto_positions:
            break

        # Fetch precio actual — DexScreener con fallback a PumpPortal
        info = await asyncio.get_event_loop().run_in_executor(None, _fetch_token_info, mint)
        current_price = (info or {}).get("price_usd", 0)

        # Última alternativa: precio guardado en posición (del WS de compra/monitor previo)
        if current_price <= 0:
            current_price = _auto_positions[mint].get("last_price_usd", 0)
        else:
            # Actualizar último precio conocido en la posición
            _auto_positions[mint]["last_price_usd"] = current_price

        if current_price <= 0 or entry_price <= 0:
            hold_min = (time.time() - entry_time) / 60
            if hold_min >= MAX_HOLD_MIN:
                log.warning(f"[auto] ⏰ Timeout {symbol} (sin precio) — cerrando tras {hold_min:.1f}min")
                _trigger_sell(mint, symbol, 0.0, "timeout-sin-precio", program)
            else:
                log.debug(f"[auto] {symbol} sin precio aún — esperando ({hold_min:.1f}min)")
            continue

        pnl_pct = (current_price - entry_price) / entry_price * 100
        hold_min = (time.time() - entry_time) / 60
        peak_pct = _auto_positions[mint].get("peak_pct", 0)

        # Actualizar pico
        if pnl_pct > peak_pct:
            _auto_positions[mint]["peak_pct"] = pnl_pct
            peak_pct = pnl_pct

        log.info(
            f"[auto] 📊 {symbol} | P&L {pnl_pct:+.1f}% | "
            f"pico {peak_pct:+.1f}% | hold {hold_min:.1f}min"
        )

        # ── Exit conditions ──────────────────────────────────────────────
        exit_reason = None

        if pnl_pct <= STOP_LOSS_PCT:
            exit_reason = f"stop-loss {pnl_pct:+.1f}%"

        elif pnl_pct >= TAKE_PROFIT_PCT:
            exit_reason = f"take-profit {pnl_pct:+.1f}%"

        elif peak_pct >= TRAILING_PEAK and (peak_pct - pnl_pct) >= TRAILING_DROP:
            exit_reason = f"trailing-stop pico={peak_pct:+.1f}% actual={pnl_pct:+.1f}%"

        elif hold_min >= MAX_HOLD_MIN:
            exit_reason = f"timeout {hold_min:.1f}min"

        if exit_reason:
            _trigger_sell(mint, symbol, current_price, exit_reason, program)
            break


def _trigger_sell(mint: str, symbol: str, current_price_usd: float, reason: str, program: str):
    """Envía señal de venta al executor/simulator y limpia la posición."""
    if mint not in _auto_positions:
        return

    # Si el caller no tiene precio (timeout-sin-precio), usar el último conocido
    if current_price_usd <= 0:
        current_price_usd = _auto_positions[mint].get("last_price_usd", 0)

    _auto_positions.pop(mint, None)

    sol_price = _get_sol_price_usd()
    price_sol = (current_price_usd / sol_price) if current_price_usd > 0 and sol_price > 0 else 0.0

    sell_swap = {
        "wallet":           "AUTONOMOUS_BOT",
        "wallet_label":     "AUTO 🤖",
        "program":          program,
        "token_in":         mint,
        "token_out":        SOL_MINT,
        "symbol_in":        symbol,
        "symbol_out":       "SOL",
        "amount_in":        0,
        "amount_out":       0,
        "wallet_pre_sol":   0,
        "implied_price_sol": price_sol,
    }

    log.info(f"[auto] 🔴 VENTA {symbol} | motivo: {reason}")
    execute_copy(sell_swap)


# ── Evaluación de token ───────────────────────────────────────────────────────

async def _evaluate_token(mint: str):
    """Fetch + score + compra si pasa el filtro."""
    info = _tracked.get(mint, {})
    if info.get("evaluated"):
        return

    _tracked[mint]["evaluated"] = True

    # No abrir más posiciones que el límite
    if len(_auto_positions) >= MAX_POSITIONS:
        log.debug(f"[auto] Límite {MAX_POSITIONS} posiciones autónomas — skip {mint[:8]}")
        return

    symbol = info.get("symbol", mint[:6])
    ws_buys  = info.get("buys",  0)
    ws_sells = info.get("sells", 0)
    log.info(f"[auto] 🔍 Evaluando {symbol} ({mint[:8]}...) | buys WS: {ws_buys} | sells WS: {ws_sells}")

    # Intentar DexScreener + PumpPortal API
    token_info = await asyncio.get_event_loop().run_in_executor(None, _fetch_token_info, mint)

    # Fallback total: construir token_info desde datos WS acumulados en _tracked
    # Esto garantiza que SIEMPRE podemos scorear el token aunque ninguna API externa responda.
    if not token_info:
        sol_price = _get_sol_price_usd()
        v_sol     = info.get("v_sol", 0)
        v_tok     = info.get("v_tok", 0)
        mcap_sol  = info.get("mcap_sol", 0)
        age_min   = round((time.time() - info["created_at"]) / 60, 1)

        price_sol = (v_sol / 1e9) / (v_tok / 1e6) if v_sol > 0 and v_tok > 0 else info.get("last_price_sol", 0)
        price_usd = price_sol * sol_price if price_sol > 0 else 0
        mcap_usd  = mcap_sol * sol_price
        # Liquidez en bonding curve ≈ SOL que hay en la curva (no es AMM pero da referencia)
        liq_usd   = (v_sol / 1e9) * sol_price if v_sol > 0 else 0

        if price_usd <= 0 and ws_buys == 0:
            log.info(f"[auto] ❌ {symbol} — sin datos en ninguna fuente (token muerto?)")
            return

        token_info = {
            "price_usd":       price_usd,
            "price_sol":       price_sol,
            "liquidity_usd":   liq_usd,
            "mcap_usd":        mcap_usd,
            "token_age_min":   age_min,
            "program":         "Pump.fun",
            "buys_5m":         ws_buys,
            "sells_5m":        ws_sells,
            "price_change_1h": 0.0,
            "price_change_5m": 0.0,
            "pair_address":    "",
        }
        log.info(
            f"[auto] 📡 {symbol} — datos desde WS (sin DexScreener) | "
            f"edad {age_min:.1f}min | mcap ${mcap_usd:,.0f} | liq ${liq_usd:,.0f} | buys {ws_buys}"
        )

    # Combinar buys WS con los de DexScreener (el más alto gana)
    token_info["buys_5m"] = max(token_info.get("buys_5m", 0), ws_buys)
    token_info["sells_5m"] = max(token_info.get("sells_5m", 0), ws_sells)

    score, passed, reason = score_token(token_info)
    log.info(
        f"[auto] {'✅ COMPRAR' if passed else '❌ SKIP'} {symbol} | "
        f"score={score} | {reason}"
    )

    if not passed:
        return

    entry_price = token_info.get("price_usd", 0)
    if entry_price <= 0:
        log.warning(f"[auto] {symbol} pasó el scorer pero precio USD=0 — skip")
        return

    # Registrar posición antes de ejecutar para evitar duplicados
    _auto_positions[mint] = {
        "entry_price_usd": entry_price,
        "last_price_usd":  entry_price,  # se actualiza en cada tick del monitor
        "entry_time":      time.time(),
        "peak_pct":        0.0,
        "symbol":          symbol,
        "program":         token_info.get("program", "Pump.fun"),
    }

    buy_swap = {
        "wallet":           "AUTONOMOUS_BOT",
        "wallet_label":     "AUTO 🤖",
        "program":          token_info.get("program", "Pump.fun"),
        "token_in":         SOL_MINT,
        "token_out":        mint,
        "symbol_in":        "SOL",
        "symbol_out":       symbol,
        "amount_in":        0,   # executor usa MAX_TRADE_PCT del balance propio
        "amount_out":       0,
        "wallet_pre_sol":   0,
        "implied_price_sol": token_info.get("price_sol", 0),
    }

    execute_copy(buy_swap)

    # Arrancar monitor de precio en background
    asyncio.create_task(_monitor_position(mint, symbol))


async def _schedule_eval(mint: str, delay_sec: float):
    """Espera delay_sec y luego evalúa el token si aún no fue evaluado."""
    await asyncio.sleep(delay_sec)
    if mint in _tracked and not _tracked[mint].get("evaluated"):
        await _evaluate_token(mint)
    # Limpiar tracking tras evaluación
    _tracked.pop(mint, None)


# ── Handlers de mensajes PumpPortal ──────────────────────────────────────────

async def _handle_new_token(data: dict):
    """Nuevo token creado en Pump.fun — empezar a trackear."""
    mint   = data.get("mint", "")
    name   = data.get("name") or data.get("symbol") or mint[:6]
    symbol = (data.get("symbol") or name)[:8]

    if not mint or mint in _tracked:
        return

    # Guardar datos iniciales de la bonding curve que vienen en el evento create
    v_sol    = float(data.get("vSolInBondingCurve") or 0)
    v_tok    = float(data.get("vTokensInBondingCurve") or 0)
    mcap_sol = float(data.get("marketCapSol") or 0)

    _tracked[mint] = {
        "created_at":  time.time(),
        "buys":        0,
        "sells":       0,
        "symbol":      symbol,
        "name":        name,
        "evaluated":   False,
        # Datos bonding curve del WS — se actualizan en cada trade
        "v_sol":       v_sol,
        "v_tok":       v_tok,
        "mcap_sol":    mcap_sol,
        "last_price_sol": (v_sol / 1e9) / (v_tok / 1e6) if v_sol > 0 and v_tok > 0 else 0,
    }

    log.info(f"[auto] 🆕 Nuevo token: {name} ({mint[:8]}...) — evaluando en {EVAL_DELAY_MIN:.0f}min")

    # Suscribirse a trades de este token para acumular buys
    return mint  # el caller lo usa para suscribir


async def _handle_token_trade(data: dict):
    """Trade de un token trackeado — acumular buys y verificar momentum trigger."""
    mint    = data.get("mint", "")
    tx_type = data.get("txType", "")

    if mint not in _tracked:
        return
    if _tracked[mint].get("evaluated"):
        return

    # Actualizar datos de bonding curve con cada trade
    v_sol    = float(data.get("vSolInBondingCurve") or 0)
    v_tok    = float(data.get("vTokensInBondingCurve") or 0)
    mcap_sol = float(data.get("marketCapSol") or 0)
    if v_sol > 0 and v_tok > 0:
        _tracked[mint]["v_sol"]  = v_sol
        _tracked[mint]["v_tok"]  = v_tok
        _tracked[mint]["last_price_sol"] = (v_sol / 1e9) / (v_tok / 1e6)
    if mcap_sol > 0:
        _tracked[mint]["mcap_sol"] = mcap_sol

    if tx_type == "buy":
        _tracked[mint]["buys"] += 1
    elif tx_type == "sell":
        _tracked[mint]["sells"] = _tracked[mint].get("sells", 0) + 1
        buys = _tracked[mint]["buys"]

        # Momentum trigger: muchos buys antes del tiempo programado
        if buys >= MOMENTUM_BUYS:
            symbol = _tracked[mint].get("symbol", mint[:6])
            age_min = (time.time() - _tracked[mint]["created_at"]) / 60
            log.info(
                f"[auto] ⚡ MOMENTUM {symbol} | {buys} buys en {age_min:.1f}min — evaluando ahora"
            )
            await _evaluate_token(mint)
            _tracked.pop(mint, None)


# ── Loop principal ────────────────────────────────────────────────────────────

async def watch_autonomous():
    """
    Loop autónomo: suscribe a PumpPortal para todos los tokens nuevos.
    Se integra en watch_all() via asyncio.gather.
    """
    log.info("[auto] 🤖 Scanner autónomo iniciado")
    log.info(
        f"[auto] Config: eval en {EVAL_DELAY_MIN}min | "
        f"momentum trigger {MOMENTUM_BUYS} buys | "
        f"SL {STOP_LOSS_PCT:+.0f}% | TP +{TAKE_PROFIT_PCT:.0f}% | "
        f"trailing >{TRAILING_PEAK:.0f}% cae -{TRAILING_DROP:.0f}% | "
        f"max hold {MAX_HOLD_MIN:.0f}min | max {MAX_POSITIONS} posiciones"
    )

    retry_delay = 5
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    # Cola de mints que necesitan suscripción a trades
    pending_subs: list[str] = []

    while True:
        try:
            async with websockets.connect(
                PUMPPORTAL_WS,
                ssl=ssl_ctx,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=5 * 1024 * 1024,
            ) as ws:
                retry_delay = 5
                log.info("[auto] PumpPortal WS conectado — suscribiendo a nuevos tokens")

                # Suscribirse a nuevos tokens
                await ws.send(json.dumps({"method": "subscribeNewToken"}))

                async for raw in ws:
                    try:
                        data = json.loads(raw)
                    except Exception:
                        continue

                    # ACK de suscripción
                    if "message" in data and not data.get("mint"):
                        continue

                    tx_type = data.get("txType", "")
                    mint    = data.get("mint", "")

                    if not mint:
                        continue

                    # Token nuevo (create)
                    if tx_type == "create" or (not tx_type and mint not in _tracked):
                        result = await _handle_new_token(data)
                        if result:
                            # Suscribir a trades de este token
                            await ws.send(json.dumps({
                                "method": "subscribeTokenTrade",
                                "keys":   [result],
                            }))
                            # Programar evaluación por tiempo
                            asyncio.create_task(
                                _schedule_eval(result, EVAL_DELAY_MIN * 60)
                            )

                    # Trade de token trackeado
                    elif tx_type in ("buy", "sell"):
                        await _handle_token_trade(data)

        except websockets.ConnectionClosed as e:
            log.warning(f"[auto] WS cerrado ({e.code}) — reconectando en {retry_delay:.0f}s")
        except OSError as e:
            log.error(f"[auto] Error de red: {e} — reconectando en {retry_delay:.0f}s")
        except Exception as e:
            log.error(f"[auto] Error inesperado: {e} — reconectando en {retry_delay:.0f}s")

        jitter = retry_delay * random.uniform(0.8, 1.4)
        await asyncio.sleep(jitter)
        retry_delay = min(retry_delay * 1.5, 120)

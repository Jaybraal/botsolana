"""
Watcher — monitorea wallets objetivo via WebSocket.
Dos fuentes en paralelo:
  1. Helius (logsSubscribe) — todos los DEX (Jupiter, Raydium, Orca, PumpSwap…)
  2. PumpPortal WS          — Pump.fun bonding curve, notificaciones pre-confirmación

PumpPortal avisa antes de confirmación de bloque (~0.5s vs ~2-3s de Helius),
lo que reduce significativamente la latencia para tokens en Pump.fun BC.
"""

import json
import asyncio
import ssl
import certifi
import websockets
import httpx
import time
import random
from datetime import datetime

from config import RPC_HTTP, RPC_WS, TARGET_WALLETS, WALLET_LABELS, TOKENS
from copytrade.decoder import detect_swap
from copytrade.executor import execute_copy
from utils.logger import get_logger

log = get_logger("watcher")

# Cola de swaps: los watchers producen, el consumer consume en su propio coroutine
# maxsize=200 evita acumulación infinita — si la cola se llena, se descarta el swap más viejo
_swap_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

SOL_MINT         = TOKENS["SOL"]
PUMPPORTAL_WS    = "wss://pumpportal.fun/api/data"
# Wallets cuyas transacciones queremos ver en PumpPortal
# (filtramos solo las que realmente están en TARGET_WALLETS)
_PP_TARGET_SET   = set(TARGET_WALLETS)


async def subscribe_wallet(ws, wallet: str, sub_id: int):
    """Suscribe a logs de una wallet."""
    payload = {
        "jsonrpc": "2.0",
        "id":      sub_id,
        "method":  "logsSubscribe",
        "params": [
            {"mentions": [wallet]},
            {"commitment": "confirmed"}
        ]
    }
    await ws.send(json.dumps(payload))
    log.info(f"Suscrito a wallet: {wallet[:8]}...{wallet[-4:]}")


def fetch_transaction(sig: str) -> dict | None:
    """Obtiene los detalles completos de una transacción."""
    try:
        r = httpx.post(RPC_HTTP, json={
            "jsonrpc": "2.0",
            "id":      1,
            "method":  "getTransaction",
            "params":  [
                sig,
                {
                    "encoding":                       "json",
                    "maxSupportedTransactionVersion": 0,
                    "commitment":                     "confirmed",
                }
            ]
        }, timeout=15)
        data = r.json()
        return data.get("result")
    except Exception as e:
        log.error(f"Error fetching tx {sig[:12]}...: {e}")
        return None


# Control de deduplicación: no copiar la misma sig dos veces
seen_sigs: set = set()
# Control de rate: max 1 copy cada 0.5s por (wallet, token_pair) — permite trades rápidos en tokens distintos
last_copy: dict = {}


async def handle_message(msg: str):
    """Procesa cada mensaje del WebSocket."""
    try:
        data = json.loads(msg)

        # Solo nos importan las notificaciones (no las confirmaciones de suscripción)
        if data.get("method") != "logsNotification":
            return

        result = data.get("params", {}).get("result", {})
        value  = result.get("value", {})
        sig    = value.get("signature", "")
        err    = value.get("err")

        if err or not sig:
            return

        if sig in seen_sigs:
            return
        seen_sigs.add(sig)

        log.debug(f"Nueva tx detectada: {sig[:16]}...")

        # Fetch detalles
        tx = fetch_transaction(sig)
        if not tx:
            log.debug(f"TX no encontrada (aún procesando?): {sig[:16]}...")
            return

        # Detectar si es swap
        swap = detect_swap(tx)
        if not swap:
            return

        # Solo procesar wallets que seguimos explícitamente
        wallet_addr = swap["wallet"]
        if wallet_addr not in WALLET_LABELS:
            return
        label = WALLET_LABELS[wallet_addr]
        swap["wallet_label"] = label

        # Timestamp real del bloque — medir desde cuándo la wallet compró, no desde cuando detectamos
        block_time = tx.get("blockTime")
        if block_time:
            latency_s = time.time() - float(block_time)
            swap["wallet_buy_time"] = float(block_time)
            latency_str = f" | latencia [white]{latency_s:.1f}s[/]"
        else:
            latency_str = ""

        ts = datetime.now().strftime("%H:%M:%S")
        log.info(
            f"[{ts}] SWAP detectado | [bold cyan]{label}[/] | "
            f"[yellow]{swap['symbol_in']}[/] → [green]{swap['symbol_out']}[/] | "
            f"Programa: {swap['program']} | "
            f"Amount in: {swap['amount_in']:,}"
            f"{latency_str}"
        )

        # Rate limit por (wallet, token_pair) — permite trades rápidos en tokens distintos
        wallet     = swap["wallet"]
        token_pair = (swap.get("token_in", ""), swap.get("token_out", ""))
        rate_key   = (wallet, token_pair)
        now        = time.time()
        if now - last_copy.get(rate_key, 0) < 0.5:
            log.warning(f"Rate limit: ignorando copy de {wallet[:8]}... (mismo par <0.5s)")
            return
        last_copy[rate_key] = now

        # Poner en cola — no bloquea el WebSocket, el consumer async lo procesa
        try:
            _swap_queue.put_nowait(swap)
        except asyncio.QueueFull:
            log.warning(f"[Helius] Cola llena — descartando swap de {label} ({swap['symbol_out']})")

    except Exception as e:
        log.error(f"Error procesando mensaje: {e}")


async def watch():
    """Loop principal del watcher Helius."""
    if not TARGET_WALLETS:
        log.error("No hay wallets en TARGET_WALLETS. Configura el .env.")
        return

    log.info(f"Monitoreando {len(TARGET_WALLETS)} wallet(s):")
    for w in TARGET_WALLETS:
        log.info(f"  → {w}")

    retry_delay = 5
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    while True:
        try:
            async with websockets.connect(
                RPC_WS,
                ssl=ssl_ctx,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=10 * 1024 * 1024,
            ) as ws:
                log.info(f"WebSocket conectado: {RPC_WS[:40]}...")
                retry_delay = 5

                for i, wallet in enumerate(TARGET_WALLETS):
                    await subscribe_wallet(ws, wallet, i + 1)

                async for msg in ws:
                    await handle_message(msg)

        except websockets.ConnectionClosed as e:
            log.warning(f"[Helius] WebSocket cerrado ({e.code}). Reconectando en {retry_delay:.0f}s...")
        except OSError as e:
            log.error(f"[Helius] Error de red: {e}. Reconectando en {retry_delay:.0f}s...")
        except Exception as e:
            log.error(f"[Helius] Error inesperado: {e}. Reconectando en {retry_delay:.0f}s...")

        jitter = retry_delay * random.uniform(0.8, 1.4)
        await asyncio.sleep(jitter)
        retry_delay = min(retry_delay * 1.5, 120)


def _pumpportal_to_swap(data: dict) -> dict | None:
    """
    Convierte un mensaje de PumpPortal WS al formato swap que espera execute_copy.
    """
    tx_type      = data.get("txType")          # "buy" | "sell"
    mint         = data.get("mint", "")
    wallet       = data.get("traderPublicKey", "")
    sol_amount   = float(data.get("solAmount",   0))
    token_amount = float(data.get("tokenAmount", 0))
    pool         = data.get("pool", "pump")    # "pump" | "pumpswap"

    if not wallet or not mint or tx_type not in ("buy", "sell"):
        return None

    # Solo procesar wallets que estamos siguiendo
    if wallet not in _PP_TARGET_SET:
        return None

    program      = "Pump.fun" if pool == "pump" else "PumpSwap"
    sol_lamports = int(sol_amount * 1_000_000_000)

    # PumpPortal da tokenAmount en UI (no en unidades mínimas).
    # Calculamos el precio implícito aquí donde tenemos los valores correctos.
    # El simulador lo usará como fallback cuando DexScreener no tenga datos.
    implied_price_sol = (sol_amount / token_amount) if token_amount > 0 else 0.0

    if tx_type == "buy":
        return {
            "wallet":              wallet,
            "program":             program,
            "source":              "pumpportal",  # fast path en executor
            "token_in":            SOL_MINT,
            "token_out":           mint,
            "symbol_in":           "SOL",
            "symbol_out":          mint[:6],
            "amount_in":           sol_lamports,
            "amount_out":          int(token_amount),
            "wallet_pre_sol":      0,
            "implied_price_sol":   implied_price_sol,  # precio en SOL/token (UI)
        }
    else:  # sell
        return {
            "wallet":              wallet,
            "program":             program,
            "source":              "pumpportal",
            "token_in":            mint,
            "token_out":           SOL_MINT,
            "symbol_in":           mint[:6],
            "symbol_out":          "SOL",
            "amount_in":           int(token_amount),
            "amount_out":          sol_lamports,
            "wallet_pre_sol":      0,
            "implied_price_sol":   implied_price_sol,
        }


async def handle_pumpportal_message(msg: str):
    """Procesa cada mensaje del WebSocket de PumpPortal."""
    try:
        data = json.loads(msg)

        # Ignorar ACKs y mensajes de suscripción
        if "message" in data or not data.get("mint"):
            return

        sig = data.get("signature", "")
        if sig and sig in seen_sigs:
            return
        if sig:
            seen_sigs.add(sig)

        swap = _pumpportal_to_swap(data)
        if not swap:
            return

        wallet_addr  = swap["wallet"]
        label        = WALLET_LABELS.get(wallet_addr, f"{wallet_addr[:8]}...")
        swap["wallet_label"] = label

        # Timestamp del trade para medir hold real
        ts_ms = data.get("timestamp")
        if ts_ms:
            swap["wallet_buy_time"] = float(ts_ms) / 1000
            latency_s  = time.time() - swap["wallet_buy_time"]
            latency_str = f" | latencia [white]{latency_s:.1f}s[/]"
        else:
            latency_str = ""

        ts = datetime.now().strftime("%H:%M:%S")
        log.info(
            f"[{ts}] [bold magenta][PP][/] SWAP | [bold cyan]{label}[/] | "
            f"[yellow]{swap['symbol_in']}[/] → [green]{swap['symbol_out']}[/] | "
            f"{swap['program']} | "
            f"Amount: {swap['amount_in']:,}"
            f"{latency_str}"
        )

        # Rate limit igual que Helius
        token_pair = (swap.get("token_in", ""), swap.get("token_out", ""))
        rate_key   = (wallet_addr, token_pair)
        now        = time.time()
        if now - last_copy.get(rate_key, 0) < 0.5:
            log.warning(f"[PP] Rate limit: ignorando copy de {wallet_addr[:8]}... (mismo par <0.5s)")
            return
        last_copy[rate_key] = now

        # Poner en cola — el consumer async lo procesa sin bloquear el WS
        try:
            _swap_queue.put_nowait(swap)
        except asyncio.QueueFull:
            log.warning(f"[PP] Cola llena — descartando swap de {label} ({swap['symbol_out']})")

    except Exception as e:
        log.error(f"[PumpPortal] Error procesando mensaje: {e}")


async def watch_pumpportal():
    """
    Loop del watcher de PumpPortal.
    Se suscribe a trades de las wallets objetivo directamente en la bonding curve
    de Pump.fun — notifica antes de confirmación de bloque (~0.5s de latencia).
    """
    if not TARGET_WALLETS:
        return

    retry_delay = 5
    ssl_ctx     = ssl.create_default_context(cafile=certifi.where())

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
                log.info(f"[bold magenta][PumpPortal WS][/] Conectado — suscribiendo {len(TARGET_WALLETS)} wallets")
                retry_delay = 5

                # Suscribirse a trades de todas las wallets objetivo
                await ws.send(json.dumps({
                    "method": "subscribeAccountTrade",
                    "keys":   TARGET_WALLETS,
                }))

                async for msg in ws:
                    await handle_pumpportal_message(msg)

        except websockets.ConnectionClosed as e:
            log.warning(f"[PumpPortal] WebSocket cerrado ({e.code}). Reconectando en {retry_delay:.0f}s...")
        except OSError as e:
            log.error(f"[PumpPortal] Error de red: {e}. Reconectando en {retry_delay:.0f}s...")
        except Exception as e:
            log.error(f"[PumpPortal] Error inesperado (servidor): {e}. Reconectando en {retry_delay:.0f}s...")

        # Jitter para evitar reconexiones en ráfaga — distribuye carga al servidor
        jitter = retry_delay * random.uniform(0.8, 1.4)
        await asyncio.sleep(jitter)
        retry_delay = min(retry_delay * 1.5, 120)


async def _swap_consumer():
    """
    Consumer de la cola de swaps — corre en paralelo con los watchers.
    Procesa cada swap con execute_copy (async) sin bloquear los WebSockets.
    Si un trade tarda 10s, el WS sigue recibiendo mensajes sin interrupción.
    """
    while True:
        swap = await _swap_queue.get()
        try:
            await execute_copy(swap)
        except Exception as e:
            log.error(f"[consumer] Error en execute_copy: {e}")
        finally:
            _swap_queue.task_done()


async def watch_all():
    """Corre Helius, PumpPortal, scanner autónomo y ETH watcher en paralelo."""
    from utils.blockchain import detect_blockchain
    from copytrade.eth_watcher import watch_eth_wallets
    from copytrade.alchemy_webhooks import start_webhook_server, set_monitored_wallets
    from config import ETH_POLL_INTERVAL
    import os

    # Separar wallets por blockchain
    solana_wallets = [w for w in TARGET_WALLETS if detect_blockchain(w) == "solana"]
    eth_wallets = [w for w in TARGET_WALLETS if detect_blockchain(w) == "ethereum"]

    tasks = []

    # Consumer async de la cola — siempre activo si hay algo que procesar
    tasks.append(_swap_consumer())

    # Copy-trade watchers (solo si hay wallets configuradas)
    if solana_wallets:
        tasks += [watch(), watch_pumpportal()]
    else:
        log.info("[watcher] Sin TARGET_WALLETS — modo copy-trade desactivado")

    # Scanner autónomo (activo si AUTONOMOUS_MODE=true)
    if os.getenv("AUTONOMOUS_MODE", "false").lower() == "true":
        from copytrade.autonomous_scanner import watch_autonomous
        log.info("[watcher] 🤖 Modo autónomo activado — scanner sin copy wallets")
        tasks.append(watch_autonomous())

    if not tasks:
        log.error("Sin modo activo. Configura TARGET_WALLETS o pon AUTONOMOUS_MODE=true.")
        return

    if eth_wallets:
        log.info(f"Iniciando ETH watcher para {len(eth_wallets)} wallets")
        if os.getenv("ALCHEMY_API_KEY"):
            log.info("Modo: Alchemy Webhooks (< 100ms)")
            set_monitored_wallets(eth_wallets)
            webhook_port = int(os.getenv("WEBHOOK_PORT", "8000"))
            tasks.append(start_webhook_server(port=webhook_port))
        else:
            log.info(f"Modo: Polling ({ETH_POLL_INTERVAL}s) — para webhook configura ALCHEMY_API_KEY")
            tasks.append(watch_eth_wallets(eth_wallets, poll_interval=ETH_POLL_INTERVAL))

    await asyncio.gather(*tasks)

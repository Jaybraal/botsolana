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
from datetime import datetime

from config import RPC_HTTP, RPC_WS, TARGET_WALLETS, WALLET_LABELS, TOKENS
from copytrade.decoder import detect_swap
from copytrade.executor import execute_copy
from utils.logger import get_logger

log = get_logger("watcher")

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

        # Añadir etiqueta de plataforma
        wallet_addr = swap["wallet"]
        label = WALLET_LABELS.get(wallet_addr, f"{wallet_addr[:8]}...")
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

        # Ejecutar copy en hilo separado para no bloquear el WebSocket
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, execute_copy, swap)

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
            log.warning(f"[Helius] WebSocket cerrado ({e.code}). Reconectando en {retry_delay}s...")
        except OSError as e:
            log.error(f"[Helius] Error de red: {e}. Reconectando en {retry_delay}s...")
        except Exception as e:
            log.error(f"[Helius] Error inesperado: {e}. Reconectando en {retry_delay}s...")

        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 60)


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

    if tx_type == "buy":
        return {
            "wallet":         wallet,
            "program":        program,
            "token_in":       SOL_MINT,
            "token_out":      mint,
            "symbol_in":      "SOL",
            "symbol_out":     mint[:6],
            "amount_in":      sol_lamports,
            "amount_out":     int(token_amount),
            "wallet_pre_sol": 0,   # no disponible en PumpPortal
        }
    else:  # sell
        return {
            "wallet":         wallet,
            "program":        program,
            "token_in":       mint,
            "token_out":      SOL_MINT,
            "symbol_in":      mint[:6],
            "symbol_out":     "SOL",
            "amount_in":      int(token_amount),
            "amount_out":     sol_lamports,
            "wallet_pre_sol": 0,
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

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, execute_copy, swap)

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
            log.warning(f"[PumpPortal] WebSocket cerrado ({e.code}). Reconectando en {retry_delay}s...")
        except OSError as e:
            log.error(f"[PumpPortal] Error de red: {e}. Reconectando en {retry_delay}s...")
        except Exception as e:
            log.error(f"[PumpPortal] Error inesperado: {e}. Reconectando en {retry_delay}s...")

        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 60)


async def watch_all():
    """Corre Helius y PumpPortal WebSocket en paralelo."""
    await asyncio.gather(
        watch(),
        watch_pumpportal(),
    )

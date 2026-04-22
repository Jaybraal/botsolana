"""
Watcher — monitorea wallets objetivo via WebSocket.
Cuando detecta un swap, llama al executor para copiarlo.
"""

import json
import asyncio
import ssl
import certifi
import websockets
import httpx
import time
from datetime import datetime

from config import RPC_HTTP, RPC_WS, TARGET_WALLETS, WALLET_LABELS
from copytrade.decoder import detect_swap
from copytrade.executor import execute_copy
from utils.logger import get_logger

log = get_logger("watcher")


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

        ts = datetime.now().strftime("%H:%M:%S")
        log.info(
            f"[{ts}] SWAP detectado | [bold cyan]{label}[/] | "
            f"[yellow]{swap['symbol_in']}[/] → [green]{swap['symbol_out']}[/] | "
            f"Programa: {swap['program']} | "
            f"Amount in: {swap['amount_in']:,}"
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
    """Loop principal del watcher."""
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
                retry_delay = 5  # reset al reconectar bien

                for i, wallet in enumerate(TARGET_WALLETS):
                    await subscribe_wallet(ws, wallet, i + 1)

                async for msg in ws:
                    await handle_message(msg)

        except websockets.ConnectionClosed as e:
            log.warning(f"WebSocket cerrado ({e.code}). Reconectando en {retry_delay}s...")
        except OSError as e:
            log.error(f"Error de red: {e}. Reconectando en {retry_delay}s...")
        except Exception as e:
            log.error(f"Error inesperado: {e}. Reconectando en {retry_delay}s...")

        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 60)  # backoff hasta 60s máximo

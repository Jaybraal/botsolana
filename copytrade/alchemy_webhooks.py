"""Alchemy Webhooks — Recibe notificaciones en tiempo real (< 100ms) de txs Ethereum."""

import os
import json
import asyncio
from fastapi import FastAPI, Request
from utils.logger import get_logger
from config import WALLET_LABELS
from copytrade.decoder import detect_swap
from copytrade.executor import execute_copy

log = get_logger("alchemy_webhooks")

app = FastAPI()

# Almacenar wallets ETH a monitorear
MONITORED_ETH_WALLETS = set()

# Deduplicación de txs
seen_webhook_txs = set()


def decode_ethereum_swap(tx_data: dict) -> dict | None:
    """Detecta swap en Uniswap de datos webhook de Alchemy."""
    try:
        to_addr = (tx_data.get("to") or "").lower()
        func_sig = tx_data.get("functionName", "").lower()
        from_addr = tx_data.get("from", "").lower()

        # Detectar si es Uniswap
        if "uniswap" not in func_sig and to_addr not in [
            "0xe592427a0aece92de3edee1f18e0157c05861564",  # Uniswap V3
            "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",  # Uniswap V2
        ]:
            return None

        # Detectar swap functions
        if "swap" not in func_sig:
            return None

        return {
            "type": "eth_swap",
            "hash": tx_data.get("hash"),
            "from": from_addr,
            "to": to_addr,
            "timestamp": int(tx_data.get("blockNum", "0"), 16),
            "function": func_sig,
        }
    except Exception as e:
        log.error(f"Error decodificando swap: {e}")
        return None


@app.post("/api/webhook/alchemy")
async def alchemy_webhook(request: Request):
    """Endpoint que recibe notificaciones de Alchemy en tiempo real."""
    try:
        body = await request.json()

        # Validar webhook signature si Alchemy lo requiere
        # (Implementar después si es necesario)

        webhook_id = body.get("webhookId", "unknown")
        log.debug(f"Webhook recibido: {webhook_id}")

        # Procesar cada transacción
        for event in body.get("event", {}).get("activity", []):
            tx_hash = event.get("hash", "")
            from_addr = event.get("from", "").lower()

            # Verificar si la wallet es una de las monitoreadas
            if from_addr not in MONITORED_ETH_WALLETS:
                continue

            # Deduplicar
            if tx_hash in seen_webhook_txs:
                continue
            seen_webhook_txs.add(tx_hash)

            # Detectar swap
            swap = decode_ethereum_swap(event)
            if not swap:
                continue

            label = WALLET_LABELS.get(from_addr, f"{from_addr[:8]}...")
            log.info(
                f"[ETH WEBHOOK] Swap detectado | {label} | "
                f"hash {tx_hash[:16]}... | {swap.get('function', 'swap')}"
            )

            # TODO: Ejecutar copy (por ahora solo log)

        return {"status": "ok"}

    except Exception as e:
        log.error(f"Error procesando webhook: {e}")
        return {"status": "error", "message": str(e)}


def set_monitored_wallets(eth_wallets: list):
    """Configura las wallets Ethereum a monitorear."""
    global MONITORED_ETH_WALLETS
    MONITORED_ETH_WALLETS = set(w.lower() for w in eth_wallets)
    log.info(f"Webhooks configurados para {len(MONITORED_ETH_WALLETS)} wallets ETH")


async def start_webhook_server(host: str = "0.0.0.0", port: int = 8000):
    """Inicia el servidor FastAPI para recibir webhooks."""
    import uvicorn

    log.info(f"Iniciando servidor webhook en {host}:{port}")
    log.info("URL pública para Alchemy: https://tu-dominio.com/api/webhook/alchemy")

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()

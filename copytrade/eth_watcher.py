"""ETH Watcher — Monitorea wallets Ethereum via Etherscan API.
Detecta transacciones de Uniswap y replica en simulador/live.
"""

import asyncio
import httpx
import os
from datetime import datetime
from utils.logger import get_logger
from config import WALLET_LABELS
from copytrade import eth_executor, eth_simulator

log = get_logger("eth_watcher")

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
ETHERSCAN_URL = "https://api.etherscan.io/api"

# DEXs populares en Ethereum
UNISWAP_V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
UNISWAP_V2_ROUTER = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"


async def fetch_eth_transactions(wallet: str, start_block: int = 0):
    """Obtiene transacciones de una wallet en Ethereum."""
    if not ETHERSCAN_API_KEY:
        log.warning(f"ETHERSCAN_API_KEY no configurado — ETH watcher deshabilitado")
        return []

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                ETHERSCAN_URL,
                params={
                    "module": "account",
                    "action": "txlist",
                    "address": wallet,
                    "startblock": start_block,
                    "endblock": 99999999,
                    "sort": "desc",
                    "apikey": ETHERSCAN_API_KEY,
                }
            )
            data = resp.json()
            if data.get("status") == "1":
                return data.get("result", [])
    except Exception as e:
        log.error(f"Error fetching ETH txs for {wallet[:8]}...: {e}")

    return []


def detect_uniswap_swap(tx: dict) -> dict | None:
    """Detecta si una tx es un swap en Uniswap.
    Retorna info del swap si lo encuentra."""

    to_addr = (tx.get("to") or "").lower()
    input_data = tx.get("input", "")

    # Check si es una transacción hacia Uniswap
    if to_addr != UNISWAP_V3_ROUTER.lower() and to_addr != UNISWAP_V2_ROUTER.lower():
        return None

    # Signature de exactInputSingle: 414bf389 (Uniswap V3)
    # Signature de swapExactTokensForTokens: 38ed1739 (Uniswap V2)
    if input_data.startswith("0x414bf389") or input_data.startswith("0x38ed1739"):
        return {
            "type": "swap",
            "hash": tx.get("hash"),
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value": tx.get("value"),
            "timestamp": int(tx.get("timeStamp", 0)),
        }

    return None


async def watch_eth_wallets(eth_wallets: list, poll_interval: int = None):
    """Monitorea wallets Ethereum en polling.

    poll_interval: segundos entre checks (default: 3s = máximo sin rate limit)
    """
    if poll_interval is None:
        poll_interval = int(os.getenv("ETH_POLL_INTERVAL", "3"))

    tracked_txs = set()

    log.info(f"ETH Watcher iniciado para {len(eth_wallets)} wallets | polling cada {poll_interval}s")

    while True:
        try:
            for wallet in eth_wallets:
                txs = await fetch_eth_transactions(wallet)

                for tx in txs:
                    tx_hash = tx.get("hash", "")
                    if tx_hash in tracked_txs:
                        continue

                    tracked_txs.add(tx_hash)
                    swap = detect_uniswap_swap(tx)

                    if swap:
                        label = WALLET_LABELS.get(wallet, f"{wallet[:8]}...")
                        log.info(
                            f"[ETH] Swap detectado | {label} | "
                            f"hash {tx_hash[:16]}... | "
                            f"Uniswap"
                        )

                        # Ejecutar copy en Ethereum (simulación o live)
                        # Para esta prueba, asumimos que es un BUY
                        # En producción: decodificar TX para saber si es BUY o SELL
                        try:
                            await eth_executor.execute_eth_swap(
                                token_address="0x0000000000000000000000000000000000000001",  # placeholder
                                symbol="ETH_TOKEN",  # placeholder
                                wallet_label=label,
                                is_buy=True,
                                amount_usd=10.0,  # monto default para prueba
                                slippage_bps=50,
                            )
                            eth_executor.record_eth_copytrade(
                                token_address="0x0000000000000000000000000000000000000001",
                                symbol="ETH_TOKEN",
                                wallet_label=label,
                                is_buy=True,
                                simulated=not eth_executor.can_execute_eth_live(),
                            )
                        except Exception as e:
                            log.error(f"Error ejecutando ETH copy trade: {e}")

            await asyncio.sleep(poll_interval)

        except Exception as e:
            log.error(f"Error en ETH watcher: {e}")
            await asyncio.sleep(5)

"""
Ejecutor ETH — Ejecuta swaps en Uniswap V3 (live trading).
Versión simplificada para pruebas.
"""

import httpx
import json
import os
import time
from datetime import datetime
from config import ETH_RPC_HTTP, ETH_WALLET_ADDRESS, ETH_WALLET_PRIVKEY
from utils.logger import get_logger
from copytrade import eth_simulator

log = get_logger("eth_executor")

# Direcciones de contratos
UNISWAP_V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
UNISWAP_QUOTER = "0xb27F1FA9b8B0fC7683015B356325ee22E38Be62D"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# Persistencia
os.makedirs("data", exist_ok=True)
ETH_COPYTRADES_FILE = "data/eth_copytrades.json"

def _load_eth_copytrades() -> list:
    """Carga historial de copytrades en ETH."""
    if os.path.exists(ETH_COPYTRADES_FILE):
        try:
            with open(ETH_COPYTRADES_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _save_eth_copytrades(trades: list):
    """Persiste historial de copytrades."""
    try:
        with open(ETH_COPYTRADES_FILE, "w") as f:
            json.dump(trades, f, indent=2)
    except Exception as e:
        log.error(f"Error guardando ETH copytrades: {e}")

_eth_copytrades = _load_eth_copytrades()

def can_execute_eth_live() -> bool:
    """Verifica si se puede hacer live trading en ETH."""
    return bool(ETH_WALLET_ADDRESS and ETH_WALLET_PRIVKEY)

async def get_token_price(token_address: str) -> float | None:
    """Obtiene precio actual de un token en USD (fallback CoinGecko).

    En producción, usarías Uniswap V3 oracle u on-chain price.
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            # Simular precio — en realidad necesitarías un oráculo on-chain
            # Por ahora retornamos 0 para indicar que usaremos el precio implícito
            return None
    except Exception:
        return None

async def execute_eth_swap(
    token_address: str,
    symbol: str,
    wallet_label: str,
    is_buy: bool = True,
    amount_usd: float = 0,
    slippage_bps: int = 50,
) -> bool:
    """
    Ejecuta o simula un swap en Uniswap V3.

    Args:
        token_address: dirección del token
        symbol: símbolo del token
        wallet_label: nombre de la wallet fuente
        is_buy: True si es compra, False si es venta
        amount_usd: monto en USD (para live)
        slippage_bps: slippage en basis points

    Returns:
        True si éxito (simulado o real), False si falló
    """

    if not can_execute_eth_live():
        # Modo simulación
        log.info(f"[ETH-SIM] Simulando swap: {symbol} ({'BUY' if is_buy else 'SELL'})")

        # Usar precio aproximado
        price = await get_token_price(token_address) or 0.0001

        if is_buy:
            eth_simulator.process_eth_swap(
                token_address, symbol, wallet_label,
                entry_price=price, is_buy=True
            )
        else:
            eth_simulator.process_eth_swap(
                token_address, symbol, wallet_label,
                entry_price=price, is_buy=False
            )

        return True
    else:
        # Live trading — versión simplificada
        # En producción, aquí iría la integración real con Uniswap V3
        log.warning("[ETH] Live trading en Uniswap aún no completamente implementado")
        return False

def record_eth_copytrade(
    token_address: str,
    symbol: str,
    wallet_label: str,
    is_buy: bool,
    simulated: bool = True,
):
    """Registra un copytrade en ETH para el dashboard."""
    global _eth_copytrades

    trade = {
        "time": datetime.now().isoformat(),
        "time_str": datetime.now().strftime("%H:%M:%S"),
        "token": token_address,
        "symbol": symbol,
        "wallet": wallet_label,
        "type": "buy" if is_buy else "sell",
        "simulated": simulated,
        "network": "ethereum",
    }

    _eth_copytrades.append(trade)
    _save_eth_copytrades(_eth_copytrades[-100:])  # Guardar últimos 100

    log.info(
        f"[ETH] Copytrade registrado: {symbol} "
        f"({'SIM' if simulated else 'LIVE'}) vía {wallet_label}"
    )

def get_eth_copytrades() -> list:
    """Retorna historial de copytrades en ETH."""
    return _eth_copytrades

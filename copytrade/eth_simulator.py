"""
Simulador ETH — Replica exactamente lo que pasaría en Uniswap live.

Reglas de realismo:
- Capital inicial = SIM_CAPITAL (USD)
- Gas fees = dinámicos según estado actual de red (50-500 gwei)
- Slippage = dinámico según pool size en Uniswap
- Market impact = igual que Solana (no lineal)
"""

import json
import math
import os
import time
import threading
from datetime import datetime
from config import get_max_trade_pct_by_balance
from utils.logger import get_logger

log = get_logger("eth_simulator")

os.makedirs("data", exist_ok=True)
ETH_POSITIONS_FILE = "data/eth_positions.json"
ETH_HISTORY_FILE   = "data/eth_history.json"
ETH_BALANCE_FILE   = "data/eth_balance.json"

# Config simulador ETH
ETH_INITIAL_CAPITAL    = float(os.getenv("SIM_CAPITAL", "50.0"))
ETH_MIN_TRADE          = float(os.getenv("SIM_MIN_TRADE", "0.50"))
ETH_LIQUIDATION        = float(os.getenv("SIM_LIQUIDATION", "2.0"))
ETH_GAS_PRICE_GWEI     = float(os.getenv("ETH_GAS_PRICE_GWEI", "30.0"))  # gwei (realista: 20-50)
ETH_GAS_PER_SWAP       = 120000  # 120k gas típico en Uniswap V3 (vs 21k normal, 150k con slippage)
ETH_SLIPPAGE_BPS       = int(os.getenv("SLIPPAGE_BPS", "50"))  # 0.5% default

# Realismo brutal — igual que Solana
ETH_DYNAMIC_SLIPPAGE   = os.getenv("SIM_DYNAMIC_SLIPPAGE", "true").lower() == "true"
ETH_MARKET_IMPACT      = os.getenv("SIM_MARKET_IMPACT", "true").lower() == "true"
ETH_SMART_FAIL_RATE    = os.getenv("SIM_SMART_FAIL_RATE", "true").lower() == "true"
ETH_BASE_FAIL_RATE     = float(os.getenv("SIM_BASE_FAIL_RATE", "0.05"))  # 5% en ETH (más estable que Solana)

# Cargar o inicializar
if os.getenv("SIM_RESET", "false").lower() == "true":
    for _f in [ETH_POSITIONS_FILE, ETH_HISTORY_FILE, ETH_BALANCE_FILE]:
        if os.path.exists(_f):
            os.remove(_f)

def _load_eth_positions() -> dict:
    if os.path.exists(ETH_POSITIONS_FILE):
        try:
            with open(ETH_POSITIONS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _load_eth_history() -> list:
    if os.path.exists(ETH_HISTORY_FILE):
        try:
            with open(ETH_HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _load_eth_balance() -> float:
    if os.path.exists(ETH_BALANCE_FILE):
        try:
            with open(ETH_BALANCE_FILE) as f:
                return float(json.load(f).get("balance", ETH_INITIAL_CAPITAL))
        except Exception:
            pass
    return ETH_INITIAL_CAPITAL

_eth_positions: dict[str, dict] = _load_eth_positions()
_eth_history: list[dict] = _load_eth_history()
_eth_balance: float = _load_eth_balance()
_eth_lock = threading.Lock()

def _save_eth_positions():
    with open(ETH_POSITIONS_FILE, "w") as f:
        json.dump(_eth_positions, f, indent=2)

def _save_eth_history():
    with open(ETH_HISTORY_FILE, "w") as f:
        json.dump(_eth_history, f, indent=2)

def _save_eth_balance():
    with open(ETH_BALANCE_FILE, "w") as f:
        json.dump({
            "balance": round(_eth_balance, 4),
            "initial": ETH_INITIAL_CAPITAL,
            "updated_at": datetime.now().strftime("%H:%M:%S %d/%m/%Y"),
        }, f, indent=2)

def _calc_gas_fee_usd() -> float:
    """Calcula fee de gas en USD.

    Gas típico Uniswap V3: 150k gas
    Precio gas: 50-500 gwei (configurado en .env)
    Wei a ETH: dividir por 1e18
    ETH a USD: multiplicar por precio actual (~$2300)
    """
    eth_price = 2300.0  # Aproximado — idealmente fetched from API
    wei = ETH_GAS_PRICE_GWEI * 1e9 * ETH_GAS_PER_SWAP
    eth_amount = wei / 1e18
    return eth_amount * eth_price

def _calc_slippage_eth(trade_usd: float, pool_liquidity_usd: float = 100000.0) -> float:
    """Slippage dinámico en Uniswap.

    Pools de token nuevo típicamente tienen poca liquidity.
    Base: 0.5% (SLIPPAGE_BPS)
    Dinámico: +0.5% por cada 1% del tamaño del pool
    """
    if not ETH_DYNAMIC_SLIPPAGE or pool_liquidity_usd <= 0:
        return ETH_SLIPPAGE_BPS / 10000

    ratio = trade_usd / pool_liquidity_usd
    dynamic_slippage = (ETH_SLIPPAGE_BPS / 10000) + (ratio * 0.5)
    return min(dynamic_slippage, 0.20)  # cap 20%

def _calc_market_impact_eth(trade_usd: float, pool_liquidity_usd: float = 100000.0) -> float:
    """Market impact en Uniswap (raíz cuadrada como AMM)."""
    if not ETH_MARKET_IMPACT or pool_liquidity_usd <= 0:
        return 0.0

    ratio = trade_usd / pool_liquidity_usd
    impact = math.sqrt(ratio) * 0.30
    return min(impact, 0.35)

def _calc_fail_rate_eth() -> float:
    """TX fail rate en Ethereum (más baja que Solana)."""
    if not ETH_SMART_FAIL_RATE:
        return ETH_BASE_FAIL_RATE

    # Ethereum es más estable, fail rate base más bajo
    return min(ETH_BASE_FAIL_RATE, 0.05)  # máximo 5%

def _get_trade_amount_eth() -> float:
    """Cantidad a invertir según balance actual."""
    pct = get_max_trade_pct_by_balance(_eth_balance)
    amount = _eth_balance * pct
    return max(ETH_MIN_TRADE, amount)

def process_eth_swap(token_address: str, symbol: str, wallet_label: str,
                     entry_price: float = 0.0, is_buy: bool = True):
    """Procesa un swap simulado en Ethereum.

    Args:
        token_address: dirección del token (0x...)
        symbol: símbolo del token (ej: "USDC")
        wallet_label: nombre de la wallet que copiar
        entry_price: precio de entrada en USD
        is_buy: True si es compra, False si es venta
    """
    global _eth_balance

    if not is_buy:
        # Venta — cerrar posición
        _handle_eth_sell(token_address, symbol, wallet_label, entry_price)
    else:
        # Compra — abrir posición
        _handle_eth_buy(token_address, symbol, wallet_label, entry_price)

def _handle_eth_buy(token_addr: str, symbol: str, label: str, entry_price: float):
    """Abre posición simulada en ETH."""
    global _eth_balance

    if _eth_balance < ETH_LIQUIDATION:
        log.warning(f"[ETH-SIM] Balance ${_eth_balance:.2f} < mínimo ${ETH_LIQUIDATION} — cancelado")
        return

    with _eth_lock:
        existing = _eth_positions.get(token_addr)

        # Posición ya abierta — escalar
        if existing and existing.get("entry_price"):
            confirmations = existing.get("confirmations", 1)
            if confirmations < 3:  # máx 3 confirmaciones
                extra = round(_get_trade_amount_eth() * 0.5, 4)
                old_amount = existing["amount_usd"]
                old_price = existing["entry_price"]
                new_amount = round(old_amount + extra, 4)
                new_price = (old_price * old_amount + entry_price * extra) / new_amount if new_amount > 0 else old_price

                existing["amount_usd"] = new_amount
                existing["entry_price"] = round(new_price, 10)
                existing["confirmations"] = confirmations + 1
                _save_eth_positions()

                log.info(
                    f"[ETH-SIM] 🔥 CONFIRMACIÓN #{confirmations + 1} | "
                    f"[cyan]{label}[/] también compró [yellow]{symbol}[/] | "
                    f"añadido [green]+${extra:.2f}[/]"
                )
            return

        if existing:  # placeholder
            return

        _eth_positions[token_addr] = {}  # placeholder

    trade_amount = _get_trade_amount_eth()
    if trade_amount < ETH_MIN_TRADE:
        log.debug(f"[ETH-SIM] Trade ${trade_amount:.2f} < mínimo — ignorando")
        return

    # Calcular costos
    gas_fee_usd = _calc_gas_fee_usd()
    slippage = _calc_slippage_eth(trade_amount)
    impact = _calc_market_impact_eth(trade_amount)
    total_cost_pct = slippage + impact + (gas_fee_usd / trade_amount if trade_amount > 0 else 0)

    # Fail rate
    fail_rate = _calc_fail_rate_eth()
    failed = True if (time.time() % 1.0) < fail_rate else False

    if failed:
        log.warning(f"[ETH-SIM] ❌ TX FALLÓ en Uniswap (rate={fail_rate*100:.1f}%) — {symbol}")
        with _eth_lock:
            _eth_positions.pop(token_addr, None)
        return

    # Actualizar estado
    with _eth_lock:
        _eth_positions[token_addr] = {
            "symbol": symbol,
            "wallet_label": label,
            "amount_usd": round(trade_amount, 4),
            "entry_price": round(entry_price, 10),
            "gas_fee": round(gas_fee_usd, 4),
            "opened_at": time.time(),
            "confirmations": 1,
        }
        _eth_balance = round(_eth_balance - trade_amount - gas_fee_usd, 4)
        _save_eth_positions()
        _save_eth_balance()

    log.info(
        f"[ETH-SIM] 💰 COMPRA | [cyan]{label}[/] → [yellow]{symbol}[/] | "
        f"${trade_amount:.2f} @ ${entry_price:.4f} | "
        f"gas: ${gas_fee_usd:.4f} | slippage: {slippage*100:.2f}% | "
        f"balance: [{'green' if _eth_balance > 0 else 'red'}]${_eth_balance:.2f}[/]"
    )

    _eth_history.append({
        "timestamp": datetime.now().isoformat(),
        "type": "buy",
        "symbol": symbol,
        "token": token_addr,
        "amount": round(trade_amount, 4),
        "price": round(entry_price, 10),
        "gas_fee": round(gas_fee_usd, 4),
        "wallet": label,
    })
    _save_eth_history()

def _handle_eth_sell(token_addr: str, symbol: str, label: str, sell_price: float):
    """Cierra posición simulada en ETH."""
    global _eth_balance

    with _eth_lock:
        pos = _eth_positions.get(token_addr)
        if not pos or not pos.get("entry_price"):
            return

        entry = pos["entry_price"]
        amount = pos["amount_usd"]
        gas_fee = _calc_gas_fee_usd()

        # P&L
        pnl = (sell_price - entry) * (amount / entry)
        pnl_usd = round(pnl, 4)
        net_pnl = round(pnl_usd - gas_fee, 4)

        _eth_balance = round(_eth_balance + amount + net_pnl, 4)
        _eth_positions.pop(token_addr, None)
        _save_eth_positions()
        _save_eth_balance()

    pnl_color = "green" if net_pnl > 0 else "red"
    log.info(
        f"[ETH-SIM] 📤 VENTA | [yellow]{symbol}[/] @ ${sell_price:.4f} | "
        f"PnL: [{pnl_color}]${net_pnl:.2f}[/] | balance: ${_eth_balance:.2f}"
    )

    _eth_history.append({
        "timestamp": datetime.now().isoformat(),
        "type": "sell",
        "symbol": symbol,
        "token": token_addr,
        "sell_price": round(sell_price, 10),
        "pnl": net_pnl,
    })
    _save_eth_history()

def get_eth_balance() -> float:
    """Retorna balance simulado actual en ETH."""
    return round(_eth_balance, 4)

def get_eth_positions() -> dict:
    """Retorna posiciones abiertas en ETH."""
    return dict(_eth_positions)

def get_eth_stats() -> dict:
    """Retorna estadísticas simuladas de ETH."""
    pnl_total = sum(h.get("pnl", 0) for h in _eth_history if h.get("type") == "sell")
    trades = len(_eth_history)

    return {
        "balance": round(_eth_balance, 4),
        "initial": ETH_INITIAL_CAPITAL,
        "open_positions": len(_eth_positions),
        "total_pnl": round(pnl_total, 4),
        "total_trades": trades,
        "return_pct": round(((_eth_balance - ETH_INITIAL_CAPITAL) / ETH_INITIAL_CAPITAL * 100) if ETH_INITIAL_CAPITAL > 0 else 0, 2),
    }

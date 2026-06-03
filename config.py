import os
from dotenv import load_dotenv
load_dotenv()

# --- RPC Solana ---
RPC_HTTP = os.getenv("SOLANA_RPC_HTTP", "https://api.mainnet-beta.solana.com")
RPC_WS   = os.getenv("SOLANA_RPC_WS",  "wss://api.mainnet-beta.solana.com")
# Fallback WS cuando el primario devuelve 429 (ej: cuota Helius agotada)
RPC_WS_FALLBACK = os.getenv("SOLANA_RPC_WS_FALLBACK", "wss://api.mainnet-beta.solana.com")

# --- RPC Ethereum ---
ETH_RPC_HTTP = os.getenv("ETH_RPC_HTTP", "https://eth.llamarpc.com")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY", "")
ETH_WALLET_ADDRESS = os.getenv("ETH_WALLET_ADDRESS", "")
ETH_WALLET_PRIVKEY = os.getenv("ETH_WALLET_PRIVKEY", "")
ETH_POLL_INTERVAL = int(os.getenv("ETH_POLL_INTERVAL", "3"))  # 3s mínimo sin rate limit
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8000"))

# --- Modo live/simulación ---
# Poner LIVE_MODE=false en Railway para pausar trading real sin borrar las keys.
_LIVE_MODE = os.getenv("LIVE_MODE", "true").lower() == "true"

# --- Tu wallet ---
WALLET_PUBKEY     = os.getenv("WALLET_PUBKEY", "") if _LIVE_MODE else ""
WALLET_PRIVKEY    = os.getenv("WALLET_PRIVKEY_B58", "") if _LIVE_MODE else ""

# --- Wallets a copiar ---
_raw = os.getenv("TARGET_WALLETS", "")
TARGET_WALLETS = [w.strip() for w in _raw.split(",") if w.strip()]

# Etiquetas para los logs — identifica qué wallet/plataforma generó cada copy
WALLET_LABELS: dict[str, str] = {
    "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o": "Cented",
    "3LUfv2u5yzsDtUzPdsSJ7ygPBuqwfycMkjpNreRR2Yww": "Domy",
    "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt": "Theo",
    "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f": "Cupsey ⭐",
    "6S8GezkxYUfZy9JPtYnanbcZTMB87Wjt1qx3c6ELajKC": "Nyhrox",
    "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9": "Decu",
    "DuQabFqdC9eeBULVa7TTdZYxe8vK8ct5DZr4Xcf7docy": "Orange",
    "7SDs3PjT2mswKQ7Zo4FTucn9gJdtuW4jaacPA65BseHS": "Insentos",
    "831yhv67QpKqLBJjbmw2xoDUeeFHGUx8RnuRj9imeoEs": "Trey",
    "DxM1hfY8FQ8dNGrucuJzhJcF8KRbjk8WBwrgKvQ9spPv": "RC",
    "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk": "Cupsey-2",
    "0xdc6b9d500d26ac3dd43f783f4ada4d6c37205232": "ETH-Wallet-1",
    "0xb3b088d37f03f82e8caaf019191dbaab6bf9d6cd": "ETH-Wallet-2",
}

# --- Weighted Wallet Allocation (NUEVO) ---
# Asigna porcentaje de capital dinámicamente según performance histórica
# Basado en win rate real de cada wallet
WALLET_WEIGHTS: dict[str, float] = {
    "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk": 0.40,  # Cupsey-2: 61.5% WR → 40%
    "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9": 0.30,  # Decu: 56.2% WR → 30%
    "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o": 0.20,  # Cented: 44.4% WR → 20%
    "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f": 0.10,  # Cupsey: 25.0% WR → 10%
}

DYNAMIC_REWEIGHT = os.getenv("DYNAMIC_REWEIGHT", "true").lower() == "true"
REWEIGHT_INTERVAL_HOURS = 24  # Recalcular weights cada 24h

# --- Config del bot (copy trade) ---
SLIPPAGE_BPS     = int(os.getenv("SLIPPAGE_BPS", "75"))   # 75 = 0.75% (optimizado)

# --- Modo proporcional ---
# El bot invierte el mismo % del capital que invirtió la wallet objetivo.
# Ej: wallet tenía 10 SOL y metió 0.5 SOL (5%) → nosotros metemos 5% de nuestro balance.
PROPORTIONAL_MODE = os.getenv("PROPORTIONAL_MODE", "true").lower() == "true"

# Tope máximo: basado en balance actual (risk management).
# Tabla dinámica según rango del balance en USD:
# - $50–$200: 10% por trade (reducido tras pérdidas live con 25%)
# - $200–$1k: 12% por trade
# - $1k–$5k: 7% por trade
# - $5k+:    3% por trade
RISK_TIERS: list[tuple[float, float]] = [
    (50, 0.10),      # $50-$200: 10%
    (200, 0.12),     # $200-$1k: 12%
    (1000, 0.07),    # $1k-$5k: 7%
    (float('inf'), 0.03),  # $5k+: 3%
]

def get_max_trade_pct_by_balance(balance_usd: float) -> float:
    """Retorna el % máximo por trade según el balance en USD."""
    if balance_usd >= 5000:
        return 0.03    # $5k+: 3%
    elif balance_usd >= 1000:
        return 0.07    # $1k–$5k: 7%
    elif balance_usd >= 200:
        return 0.12    # $200–$1k: 12%
    else:
        return 0.10    # $50–$200: 10% (reducido para limitar pérdida por trade)

# Fallback para compatibilidad — se usa si no hay balance calculado
# AJUSTADO A 3.5% para viabilidad con weighted allocation
# Con ponderación: efectivo = 0.5-2.8% según wallet (vs 3.5%)
MAX_TRADE_PCT  = float(os.getenv("MAX_TRADE_PCT",  "0.035"))  # 3.5% máximo por trade

# Mínimo en lamports por trade (evita trades de polvo que no cubren las fees).
MIN_TRADE_SOL  = float(os.getenv("MIN_TRADE_SOL",  "0.005"))  # en SOL

# Máximo de posiciones abiertas simultáneamente.
# Sin límite — se abren y cierren todas las que se puedan en paralelo.
MAX_OPEN_COPIES = int(os.getenv("MAX_OPEN_COPIES", "999"))

# --- Protección de capital ---
# Si el balance cae por debajo de este % del capital inicial, el bot deja de operar.
# 0.70 = parar si perdemos más del 30% del capital de inicio.
STOP_LOSS_PCT   = float(os.getenv("STOP_LOSS_PCT",  "0.70"))

# Pérdida máxima en la sesión actual — circuit breaker de seguridad.
# Si el balance cae más de este % desde el primer trade, todos los trades se detienen automáticamente.
# 0.20 = parar si perdemos >20% en la sesión actual.
MAX_SESSION_LOSS_PCT = float(os.getenv("MAX_SESSION_LOSS_PCT", "0.20"))

# Reserva mínima de SOL que nunca se toca (para pagar fees de red).
# 0.01 SOL ≈ $1.50 — cubre ~100 transacciones de Solana.
MIN_RESERVE_SOL = float(os.getenv("MIN_RESERVE_SOL", "0.01"))

# Price impact máximo aceptable. Sobre este % se aborta el trade.
MAX_PRICE_IMPACT = float(os.getenv("MAX_PRICE_IMPACT", "2.0"))

# Escalado progresivo del tamaño de trade según ganancia acumulada.
# Cuando el balance supera cada umbral respecto al capital inicial,
# el % máximo por trade sube — usando ganancias, no el capital base.
# Formato: (ganancia_mínima_sobre_capital_inicial, max_trade_pct)
SCALING_TIERS: list[tuple[float, float]] = [
    (0.00, MAX_TRADE_PCT),  # base:         5% por trade
    (0.10, 0.07),           # +10% profit:  7% por trade
    (0.30, 0.10),           # +30% profit: 10% por trade
    (0.60, 0.12),           # +60% profit: 12% por trade
    (1.00, 0.15),           # +100% profit: 15% por trade
    (2.00, 0.18),           # +200% profit: 18% por trade
    (4.00, 0.22),           # +400% profit: 22% por trade
    (7.00, 0.25),           # +700% profit: 25% por trade
    (10.0, 0.30),           # +1000% profit: 30% por trade
    (20.0, 0.35),           # +2000% profit: 35% por trade
    (35.0, 0.40),           # +3500% profit: 40% por trade
]

# --- Programas conocidos en Solana ---
JUPITER_V6      = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
RAYDIUM_AMM     = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
ORCA_WHIRLPOOL  = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
RAYDIUM_CLMM    = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
PUMPFUN_BC      = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"  # Pump.fun bonding curve
PUMPSWAP_AMM    = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"  # PumpSwap AMM v2
METEORA_DLMM    = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"  # Meteora DLMM

SWAP_PROGRAMS = {
    JUPITER_V6, RAYDIUM_AMM, ORCA_WHIRLPOOL, RAYDIUM_CLMM,
    PUMPFUN_BC, PUMPSWAP_AMM, METEORA_DLMM,
}

# --- Jupiter API ---
JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL  = "https://quote-api.jup.ag/v6/swap"

# --- Tokens conocidos (mint addresses) ---
TOKENS = {
    "SOL":   "So11111111111111111111111111111111111111112",
    "USDC":  "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT":  "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "BONK":  "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP":   "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "RAY":   "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "ORCA":  "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
    "WIF":   "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "PYTH":  "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "JITO":  "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
    "MSOL":  "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
    "WBTC":  "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",
    "WETH":  "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
}

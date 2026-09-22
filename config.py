import os
from dotenv import load_dotenv
load_dotenv()

# --- RPC Solana ---
RPC_HTTP = os.getenv("SOLANA_RPC_HTTP", "https://api.mainnet-beta.solana.com")
RPC_WS   = os.getenv("SOLANA_RPC_WS",  "wss://api.mainnet-beta.solana.com")
# Fallback WS cuando el primario devuelve 429 (ej: cuota Helius agotada)
RPC_WS_FALLBACK = os.getenv("SOLANA_RPC_WS_FALLBACK", "wss://api.mainnet-beta.solana.com")
# Fallback HTTP para getTransaction cuando el primario devuelve 429 (ej: cuota Helius agotada)
RPC_HTTP_FALLBACK = os.getenv("SOLANA_RPC_HTTP_FALLBACK", "https://api.mainnet-beta.solana.com")

# --- RPC Ethereum ---
ETH_RPC_HTTP = os.getenv("ETH_RPC_HTTP", "https://eth.llamarpc.com")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY", "")
ETH_WALLET_ADDRESS = os.getenv("ETH_WALLET_ADDRESS", "")
ETH_WALLET_PRIVKEY = os.getenv("ETH_WALLET_PRIVKEY", "")
ETH_POLL_INTERVAL = int(os.getenv("ETH_POLL_INTERVAL", "3"))  # 3s mínimo sin rate limit
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8000"))

# --- Modo live/simulación ---
# El modo real debe requerir una decisión explícita en el entorno de despliegue.
# Un clon local, un reinicio sin variables o una prueba nunca deben operar fondos.
_LIVE_MODE = os.getenv("LIVE_MODE", "false").lower() == "true"

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
    # Candidatas nuevas 2026-08-03 (ver docs/superpowers/specs/2026-08-03-nuevas-wallets-copytrade-candidatas.md)
    # En fase SIM — no están en ELITE_WALLETS ni WALLET_WEIGHTS hasta graduarse.
    "DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt": "The Doc",
    "9jyqFiLnruggwNn4EQwBNFXwpbLM9hrA4hV59ytyAVVz": "Nach",
    "8rvAsDKeAcEjEkiZMug9k8v1y8mW6gQQiMobd89Uy7qR": "Casino",
    "GJA1HEbxGnqBhBifH9uQauzXSB53to5rhDrzmKxhSU65": "Latuche",
    "5B52w1ZW9tuwUduueP5J7HXz5AcGfruGoX6YoAudvyxG": "Yenni",
    "ETU3GyrUsv6UztQJxHgsBX2UoJFmq79WJe3JyDpAqGMz": "MACXBT",
    "8MaVa9kdt3NW4Q5HyNAm1X5LbR8PQRVDc1W8NMVK88D5": "Daumen",
    "CEUA7zVoDRqRYoeHTP58UHU6TR8yvtVbeLrX1dppqoXJ": "Tom",
}

# Wallets con WR real > 84% (excluyen RC 0% y Trey 48.6%)
ELITE_WALLETS: frozenset[str] = frozenset({
    "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt",  # Theo    89.3%
    "6S8GezkxYUfZy9JPtYnanbcZTMB87Wjt1qx3c6ELajKC",  # Nyhrox  87.1%
    "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f",  # Cupsey  84.9%
    "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9",  # Decu    84.4%
})

SNIPE_MODE = os.getenv("SNIPE_MODE", "false").lower() == "true"

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
# Estos topes se combinan con MAX_TRADE_PCT. El valor más bajo gana.
RISK_TIERS: list[tuple[float, float]] = [
    (50, 0.05),      # $50-$200: 5%
    (200, 0.05),     # $200-$1k: 5%
    (1000, 0.03),    # $1k-$5k: 3%
    (float('inf'), 0.03),  # $5k+: 3%
]
ABSOLUTE_MAX_TRADE_PCT = 0.02

def get_max_trade_pct_by_balance(balance_usd: float) -> float:
    """Retorna el % máximo por trade según el balance en USD."""
    if balance_usd >= 5000:
        tier = 0.03
    elif balance_usd >= 1000:
        tier = 0.03
    elif balance_usd >= 200:
        tier = 0.05
    else:
        tier = 0.05
    return min(tier, float(os.getenv("MAX_TRADE_PCT", "0.02")), ABSOLUTE_MAX_TRADE_PCT)

# Fallback para compatibilidad — se usa si no hay balance calculado
# AJUSTADO A 3.5% para viabilidad con weighted allocation
# Con ponderación: efectivo = 0.5-2.8% según wallet (vs 3.5%)
MAX_TRADE_PCT  = float(os.getenv("MAX_TRADE_PCT",  "0.02"))  # 2% máximo por trade

# Mínimo en lamports por trade (evita trades de polvo que no cubren las fees).
MIN_TRADE_SOL  = float(os.getenv("MIN_TRADE_SOL",  "0.005"))  # en SOL

# Máximo de posiciones abiertas simultáneamente.
# Limita exposición agregada y evita que varios consumers gasten el mismo balance cacheado.
MAX_OPEN_COPIES = min(int(os.getenv("MAX_OPEN_COPIES", "3")), 3)

# --- Protección de capital ---
# Si el balance cae por debajo de este % del capital inicial, el bot deja de operar.
# 0.70 = parar si perdemos más del 30% del capital de inicio.
STOP_LOSS_PCT   = float(os.getenv("STOP_LOSS_PCT",  "0.70"))

# Pérdida máxima en la sesión actual — circuit breaker de seguridad.
# Si el balance cae más de este % desde el primer trade, todos los trades se detienen automáticamente.
# 0.20 = parar si perdemos >20% en la sesión actual.
MAX_SESSION_LOSS_PCT = min(float(os.getenv("MAX_SESSION_LOSS_PCT", "0.08")), 0.08)

# Reserva mínima de SOL que nunca se toca (para pagar fees de red).
# 0.01 SOL ≈ $1.50 — cubre ~100 transacciones de Solana.
MIN_RESERVE_SOL = float(os.getenv("MIN_RESERVE_SOL", "0.01"))

# Price impact máximo aceptable. Sobre este % se aborta el trade.
MAX_PRICE_IMPACT = min(float(os.getenv("MAX_PRICE_IMPACT", "1.0")), 1.0)

# --- Hard Stop-Loss de emergencia (independiente de la wallet copiada) ---
# Si una posición cae este % desde el precio de entrada, el bot vende de inmediato
# sin esperar señal de venta de la wallet objetivo (protección propia de capital).
HARD_STOP_LOSS_PCT = min(float(os.getenv("HARD_STOP_LOSS_PCT", "12.0")), 12.0)
# Cada cuántos segundos se revisa el precio de las posiciones abiertas.
STOP_LOSS_CHECK_INTERVAL_S = float(os.getenv("STOP_LOSS_CHECK_INTERVAL_S", "5.0"))

# --- Compute Budget (priority fee real vía instrucciones on-chain) ---
# Se inyectan SetComputeUnitLimit + SetComputeUnitPrice en cada TX de compra/venta,
# calculados para que la priority fee total sea COMPUTE_UNIT_PRICE_TARGET_LAMPORTS.
COMPUTE_UNIT_LIMIT = int(os.getenv("COMPUTE_UNIT_LIMIT", "200000"))
COMPUTE_UNIT_PRICE_TARGET_LAMPORTS = int(os.getenv("COMPUTE_UNIT_PRICE_TARGET_LAMPORTS", "500000"))  # ~0.0005 SOL
# micro-lamports por CU necesarios para llegar al target de arriba con el límite de arriba.
COMPUTE_UNIT_PRICE_MICROLAMPORTS = int(
    COMPUTE_UNIT_PRICE_TARGET_LAMPORTS * 1_000_000 / COMPUTE_UNIT_LIMIT
) if COMPUTE_UNIT_LIMIT else 0

# --- Jito Block Engine (protección MEV / envío prioritario) ---
# Si falla el envío por Jito, se cae automáticamente al RPC público (nunca se
# pierde un trade solo porque Jito esté caído).
USE_JITO = os.getenv("USE_JITO", "true").lower() == "true"

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

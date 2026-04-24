import os
from dotenv import load_dotenv
load_dotenv()

# --- RPC ---
RPC_HTTP = os.getenv("SOLANA_RPC_HTTP", "https://api.mainnet-beta.solana.com")
RPC_WS   = os.getenv("SOLANA_RPC_WS",  "wss://api.mainnet-beta.solana.com")

# --- Tu wallet ---
WALLET_PUBKEY     = os.getenv("WALLET_PUBKEY", "")
WALLET_PRIVKEY    = os.getenv("WALLET_PRIVKEY_B58", "")

# --- Wallets a copiar ---
_raw = os.getenv("TARGET_WALLETS", "")
TARGET_WALLETS = [w.strip() for w in _raw.split(",") if w.strip()]

# Etiquetas para los logs — identifica qué wallet/plataforma generó cada copy
WALLET_LABELS: dict[str, str] = {
    "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f": "Cupsey",
    "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk": "Cupsey-2",
    "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9": "Decu",
    "DuQabFqdC9eeBULVa7TTdZYxe8vK8ct5DZr4Xcf7docy": "Orange",
    "7SDs3PjT2mswKQ7Zo4FTucn9gJdtuW4jaacPA65BseHS": "Insentos",
}

# --- Config del bot (copy trade) ---
SLIPPAGE_BPS     = int(os.getenv("SLIPPAGE_BPS", "100"))   # 100 = 1%

# --- Modo proporcional ---
# El bot invierte el mismo % del capital que invirtió la wallet objetivo.
# Ej: wallet tenía 10 SOL y metió 0.5 SOL (5%) → nosotros metemos 5% de nuestro balance.
PROPORTIONAL_MODE = os.getenv("PROPORTIONAL_MODE", "true").lower() == "true"

# Tope máximo: nunca gastar más de este % de nuestro balance en un solo trade.
# Protege contra wallets que van all-in de golpe.
MAX_TRADE_PCT  = float(os.getenv("MAX_TRADE_PCT",  "0.05"))  # 5% máximo por trade

# Mínimo en lamports por trade (evita trades de polvo que no cubren las fees).
MIN_TRADE_SOL  = float(os.getenv("MIN_TRADE_SOL",  "0.005"))  # en SOL

# Máximo de posiciones abiertas simultáneamente.
# Con capital pequeño, limitar a 2 para no sobreexponer.
MAX_OPEN_COPIES = int(os.getenv("MAX_OPEN_COPIES", "2"))

# --- Protección de capital ---
# Si el balance cae por debajo de este % del capital inicial, el bot deja de operar.
# 0.70 = parar si perdemos más del 30% del capital de inicio.
STOP_LOSS_PCT   = float(os.getenv("STOP_LOSS_PCT",  "0.70"))

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
    (1.00, 0.15),           # +100% profit: 15% por trade (capital doblado)
    (2.00, 0.18),           # +200% profit: 18% por trade
    (4.00, 0.22),           # +400% profit: 22% por trade
    (7.00, 0.25),           # +700% profit: 25% por trade
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

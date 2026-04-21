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
    "DuQabFqdC9eeBULVa7TTdZYxe8vK8ct5DZr4Xcf7docy": "padre.gg",
    "5d8tDay1ZDV4XVUBtTvFvQiLxDe8dz2ZCdsrkmTDcbm5": "birdeye",
}

# --- Config del bot (copy trade) ---
TRADE_AMOUNT_USD = float(os.getenv("TRADE_AMOUNT_USD", "50"))
MIN_PROFIT_USD   = float(os.getenv("MIN_PROFIT_USD", "0.5"))
SLIPPAGE_BPS     = int(os.getenv("SLIPPAGE_BPS", "50"))   # 50 = 0.5%

# --- Config del sniper ---
SNIPER_AMOUNT_USD    = float(os.getenv("SNIPER_AMOUNT_USD",    "30"))   # capital simulado por trade
SNIPER_STOP_PCT      = float(os.getenv("SNIPER_STOP_PCT",      "15"))   # stop loss inicial -15%
SNIPER_MAX_HOLD_MIN  = int(os.getenv(  "SNIPER_MAX_HOLD_MIN",  "120"))  # máx 2h si no se activa trail
SNIPER_MAX_POSITIONS = int(os.getenv(  "SNIPER_MAX_POSITIONS", "5"))    # posiciones simultáneas
SNIPER_POLL_SEC      = int(os.getenv(  "SNIPER_POLL_SEC",      "30"))   # intervalo de poll
SNIPER_SLIPPAGE_BPS  = int(os.getenv(  "SNIPER_SLIPPAGE_BPS",  "100"))
# Trailing stop — sin TP fijo, deja correr los ganadores
# Runner (tendencia sostenida): trail amplio para aguantar volatilidad
SNIPER_TRAIL_START   = float(os.getenv("SNIPER_TRAIL_START",   "20"))   # activa trail al +20%
SNIPER_TRAIL_DIST    = float(os.getenv("SNIPER_TRAIL_DIST",    "15"))   # runner: 15% bajo el pico
# Pump (explosión reciente): trail se activa antes y es más ajustado
SNIPER_TRAIL_START_PUMP = float(os.getenv("SNIPER_TRAIL_START_PUMP", "12"))  # activa trail al +12%
SNIPER_TRAIL_DIST_PUMP  = float(os.getenv("SNIPER_TRAIL_DIST_PUMP",  "8"))   # pump: 8% bajo el pico
# Condición para NO cerrar aunque el trail se toque (momentum vivo)
# Si el precio sigue subiendo fuerte, extendemos el trail en lugar de cerrar
SNIPER_HOLD_BUY_RATIO = float(os.getenv("SNIPER_HOLD_BUY_RATIO", "0.62")) # >62% compradores → momentum vivo
SNIPER_HOLD_5M_MIN    = float(os.getenv("SNIPER_HOLD_5M_MIN",    "2.0"))  # 5m > +2% → sigue subiendo
# Salida de emergencia para pumps: si ambas condiciones se cumplen, salir al instante
SNIPER_PUMP_EXIT_5M        = float(os.getenv("SNIPER_PUMP_EXIT_5M",        "-3.0"))
SNIPER_PUMP_EXIT_BUY_RATIO = float(os.getenv("SNIPER_PUMP_EXIT_BUY_RATIO", "0.42"))
# Compatibilidad con analytics (TP referencia para gráficos)
SNIPER_PROFIT_PCT    = SNIPER_TRAIL_START

# Filtros de entrada — momentum confirmado (estilo DexScreener Trending)
SNIPER_MIN_MCAP       = float(os.getenv("SNIPER_MIN_MCAP",       "50000"))  # mcap mínimo $50K
SNIPER_MAX_MCAP       = float(os.getenv("SNIPER_MAX_MCAP",       "5000000"))# mcap máximo $5M
SNIPER_MIN_TOKEN_AGE  = float(os.getenv("SNIPER_MIN_TOKEN_AGE",  "1"))      # mínimo 1h de vida (evita rugs recién lanzados)
SNIPER_MAX_TOKEN_AGE  = float(os.getenv("SNIPER_MAX_TOKEN_AGE",  "24"))     # máximo 24h
SNIPER_MIN_LIQ_USD    = float(os.getenv("SNIPER_MIN_LIQ_USD",    "30000"))  # liquidez mínima $30K
SNIPER_MIN_VOL_24H    = float(os.getenv("SNIPER_MIN_VOL_24H",    "150000")) # volumen 24h mínimo $150K
SNIPER_MIN_TXNS_24H   = int(os.getenv(  "SNIPER_MIN_TXNS_24H",   "3000"))   # transacciones 24h (proxy makers)
SNIPER_MIN_TXNS_1H    = int(os.getenv(  "SNIPER_MIN_TXNS_1H",    "150"))    # activo ahora mismo
SNIPER_MIN_CHANGE_5M  = float(os.getenv("SNIPER_MIN_CHANGE_5M",  "0"))      # 5m debe ser positivo (precio subiendo ahora)
SNIPER_MIN_CHANGE_1H  = float(os.getenv("SNIPER_MIN_CHANGE_1H",  "5"))      # +5% en 1h mínimo (entrada temprana)
SNIPER_MAX_CHANGE_1H  = float(os.getenv("SNIPER_MAX_CHANGE_1H",  "80"))     # máx +80% en 1h (evita comprar el techo)
SNIPER_MIN_CHANGE_6H  = float(os.getenv("SNIPER_MIN_CHANGE_6H",  "15"))     # tendencia confirmada 6h (solo aplica si token > 6h)
SNIPER_MIN_BUY_RATIO  = float(os.getenv("SNIPER_MIN_BUY_RATIO",  "0.58"))   # más compradores que vendedores
# Filtros anti-rug
SNIPER_MAX_VOL_MCAP_RATIO = float(os.getenv("SNIPER_MAX_VOL_MCAP_RATIO", "30"))   # Vol24h/MCap máx 30x (>30 = manipulación)
SNIPER_MIN_CHANGE_24H     = float(os.getenv("SNIPER_MIN_CHANGE_24H",     "-60"))  # no entrar si cayó > 60% en 24h total

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

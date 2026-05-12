"""
Recolector de historial on-chain para las wallets monitoreadas.

Para cada wallet copiada, descarga los últimos 30 días de transacciones,
identifica swaps SOL→token, enriquece con metadata del token (edad, liquidez,
mcap) y guarda todo en SQLite para análisis con Groq.

Uso:
    python3 data_collector/fetch_history.py

Genera: data/wallet_history.db
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.logger import get_logger

log = get_logger("fetch_history")

# ── Configuración ─────────────────────────────────────────────────────────────

RPC_HTTP         = os.getenv("SOLANA_RPC_HTTP", "https://api.mainnet-beta.solana.com")
DB_PATH          = "data/wallet_history.db"
DAYS_BACK        = 14   # últimos 14 días (suficiente para patrones)
MAX_SIGS_WALLET  = 1000 # cap por wallet — Theo tiene 30k en 30 días, no necesitamos todos
SOL_MINT         = "So11111111111111111111111111111111111111112"

# Todas las wallets a analizar (copiadas del config)
WALLETS: dict[str, str] = {
    "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt": "Theo",
    "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk": "Cupsey-2",
    "6S8GezkxYUfZy9JPtYnanbcZTMB87Wjt1qx3c6ELajKC": "Nyhrox",
    "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f": "Cupsey",
    "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o": "Cented",
    "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9": "Decu",
    "831yhv67QpKqLBJjbmw2xoDUeeFHGUx8RnuRj9imeoEs": "Trey",
    "DuQabFqdC9eeBULVa7TTdZYxe8vK8ct5DZr4Xcf7docy": "Orange",
    "3LUfv2u5yzsDtUzPdsSJ7ygPBuqwfycMkjpNreRR2Yww": "Domy",
    "7SDs3PjT2mswKQ7Zo4FTucn9gJdtuW4jaacPA65BseHS": "Insentos",
    "DxM1hfY8FQ8dNGrucuJzhJcF8KRbjk8WBwrgKvQ9spPv": "RC",
}

# Programas conocidos de swap
SWAP_PROGRAMS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc":  "Orca",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter",
}

_rpc_client = httpx.Client(timeout=20)
_dex_client = httpx.Client(timeout=10, headers={"Accept": "application/json"})
_dex_last: float = 0.0


# ── RPC helpers ───────────────────────────────────────────────────────────────

def _rpc(method: str, params: list) -> dict | None:
    for attempt in range(3):
        try:
            r = _rpc_client.post(RPC_HTTP, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params
            })
            data = r.json()
            if "error" in data:
                log.debug(f"RPC error {method}: {data['error']}")
                return None
            return data.get("result")
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                log.warning(f"RPC {method} falló: {e}")
    return None


def _dex_get(path: str) -> dict | list | None:
    global _dex_last
    wait = 0.4 - (time.monotonic() - _dex_last)
    if wait > 0:
        time.sleep(wait)
    _dex_last = time.monotonic()
    try:
        r = _dex_client.get(f"https://api.dexscreener.com{path}")
        if r.status_code == 429:
            time.sleep(5)
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── Base de datos ─────────────────────────────────────────────────────────────

def init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet          TEXT NOT NULL,
            wallet_label    TEXT,
            tx_sig          TEXT UNIQUE,
            ts              INTEGER,          -- unix timestamp
            token_mint      TEXT,
            token_symbol    TEXT,
            program         TEXT,             -- Pump.fun / PumpSwap / Raydium / Orca
            sol_spent       REAL,             -- SOL gastados
            price_usd       REAL,             -- precio token al comprar (estimado)
            mcap_usd        REAL,             -- market cap al comprar
            liquidity_usd   REAL,             -- liquidez del par
            token_age_min   REAL,             -- edad del token en minutos al comprar
            pair_created_at INTEGER,          -- unix timestamp del par
            price_change_5m REAL,             -- % cambio 5m (momento del trade)
            price_change_1h REAL,             -- % cambio 1h
            buys_5m         INTEGER,          -- compras en 5m
            sells_5m        INTEGER,
            -- Resultado (llenado cuando encontramos la venta)
            sell_ts         INTEGER,
            hold_min        REAL,
            pnl_pct         REAL,             -- % ganancia/pérdida
            outcome         TEXT              -- WIN / LOSS / UNKNOWN
        )
    """)
    conn.commit()
    return conn


# ── Obtener transacciones de la wallet ───────────────────────────────────────

def get_signatures(wallet: str, days_back: int = DAYS_BACK) -> list[str]:
    cutoff = int(time.time()) - (days_back * 86400)
    sigs = []
    before = None

    while True:
        params: list = [wallet, {"limit": 1000, "commitment": "confirmed"}]
        if before:
            params[1]["before"] = before

        result = _rpc("getSignaturesForAddress", params)
        if not result:
            break

        batch = result
        if not batch:
            break

        new_sigs = []
        stop = False
        for item in batch:
            if item.get("err"):
                continue
            block_time = item.get("blockTime") or 0
            if block_time < cutoff:
                stop = True
                break
            new_sigs.append(item["signature"])

        sigs.extend(new_sigs)
        before = batch[-1]["signature"]

        if len(sigs) >= MAX_SIGS_WALLET:
            sigs = sigs[:MAX_SIGS_WALLET]
            stop = True

        log.info(f"  {wallet[:8]}... — {len(sigs)} firmas")

        if stop or len(batch) < 1000:
            break

        time.sleep(0.2)

    return sigs


# ── Decodificar transacción: detectar swap SOL→token ─────────────────────────

def decode_buy(tx_sig: str) -> dict | None:
    result = _rpc("getTransaction", [
        tx_sig,
        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}
    ])
    if not result:
        return None

    meta    = result.get("meta") or {}
    tx_data = result.get("transaction") or {}
    msg     = (tx_data.get("message") or {})
    block_time = result.get("blockTime", 0)

    if meta.get("err"):
        return None

    # Identificar programa de swap usado
    program_used = None
    account_keys = msg.get("accountKeys") or []
    accounts = [a.get("pubkey", a) if isinstance(a, dict) else a for a in account_keys]

    for prog_id, prog_name in SWAP_PROGRAMS.items():
        if prog_id in accounts:
            program_used = prog_name
            break

    if not program_used:
        return None

    # Calcular SOL neto gastado (pre - post balance de SOL de la fee payer)
    pre_balances  = meta.get("preBalances")  or []
    post_balances = meta.get("postBalances") or []
    if not pre_balances or not post_balances:
        return None

    sol_delta = (pre_balances[0] - post_balances[0]) / 1e9
    if sol_delta <= 0.001:  # no gastó SOL relevante → no es compra
        return None

    # Encontrar token recibido (balance token que aumentó)
    pre_token  = {t["accountIndex"]: t for t in (meta.get("preTokenBalances")  or [])}
    post_token = {t["accountIndex"]: t for t in (meta.get("postTokenBalances") or [])}

    token_mint   = None
    token_amount = 0.0

    for idx, post in post_token.items():
        mint = post.get("mint", "")
        if mint == SOL_MINT:
            continue
        pre_amt  = float((pre_token.get(idx, {}).get("uiTokenAmount") or {}).get("uiAmount") or 0)
        post_amt = float((post.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        delta = post_amt - pre_amt
        if delta > token_amount:
            token_amount = delta
            token_mint   = mint

    if not token_mint:
        return None

    return {
        "tx_sig":    tx_sig,
        "ts":        block_time,
        "program":   program_used,
        "sol_spent": round(sol_delta, 6),
        "token_mint": token_mint,
    }


# ── Enriquecer con metadata de DexScreener ───────────────────────────────────

def enrich_token(mint: str, buy_ts: int) -> dict:
    data = _dex_get(f"/latest/dex/tokens/{mint}")
    if not isinstance(data, dict):
        return {}

    pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "solana"]
    if not pairs:
        return {}

    # Elegir el par con mayor liquidez
    pair = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))

    pair_created  = pair.get("pairCreatedAt") or 0     # ms epoch
    pair_created_s = pair_created // 1000 if pair_created > 1e10 else pair_created

    token_age_min = None
    if pair_created_s and buy_ts:
        age_s = buy_ts - pair_created_s
        token_age_min = round(age_s / 60, 1) if age_s > 0 else None

    liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
    mcap      = float(pair.get("marketCap") or pair.get("fdv") or 0)
    price_usd = float(pair.get("priceUsd") or 0)

    price_change = pair.get("priceChange") or {}
    txns_5m = (pair.get("txns") or {}).get("m5") or {}

    base_token = pair.get("baseToken") or {}

    return {
        "token_symbol":    base_token.get("symbol", mint[:6]),
        "price_usd":       price_usd,
        "mcap_usd":        mcap,
        "liquidity_usd":   liquidity,
        "token_age_min":   token_age_min,
        "pair_created_at": pair_created_s,
        "price_change_5m": float(price_change.get("m5") or 0),
        "price_change_1h": float(price_change.get("h1") or 0),
        "buys_5m":         int(txns_5m.get("buys") or 0),
        "sells_5m":        int(txns_5m.get("sells") or 0),
    }


# ── Detectar ventas para calcular P&L ────────────────────────────────────────

def find_sell(wallet: str, token_mint: str, after_ts: int, sigs: list[str]) -> dict | None:
    for sig in sigs:
        result = _rpc("getTransaction", [
            sig,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}
        ])
        if not result:
            continue

        meta       = result.get("meta") or {}
        block_time = result.get("blockTime", 0)

        if block_time <= after_ts or meta.get("err"):
            continue

        # Buscar si este token disminuyó (= venta)
        pre_token  = {t["accountIndex"]: t for t in (meta.get("preTokenBalances")  or [])}
        post_token = {t["accountIndex"]: t for t in (meta.get("postTokenBalances") or [])}

        for idx, pre in pre_token.items():
            if pre.get("mint") != token_mint:
                continue
            pre_amt  = float((pre.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            post_amt = float(((post_token.get(idx) or {}).get("uiTokenAmount") or {}).get("uiAmount") or 0)
            if pre_amt > post_amt and pre_amt > 0:
                pre_bal  = (result.get("meta") or {}).get("preBalances",  [0])[0]
                post_bal = (result.get("meta") or {}).get("postBalances", [0])[0]
                sol_received = (post_bal - pre_bal) / 1e9
                return {
                    "sell_ts":  block_time,
                    "sol_received": round(sol_received, 6),
                }
        time.sleep(0.05)
    return None


# ── Loop principal ────────────────────────────────────────────────────────────

def collect(db_path: str = DB_PATH, days_back: int = DAYS_BACK):
    conn = init_db(db_path)
    cursor = conn.cursor()

    total_inserted = 0

    for wallet, label in WALLETS.items():
        log.info(f"\n{'─'*50}")
        log.info(f"Wallet: {label} ({wallet[:12]}...)")

        sigs = get_signatures(wallet, days_back)
        log.info(f"  {len(sigs)} transacciones en los últimos {days_back} días")

        buys_found = 0

        for sig in sigs:
            # Saltar si ya existe en DB
            cursor.execute("SELECT 1 FROM trades WHERE tx_sig = ?", (sig,))
            if cursor.fetchone():
                continue

            buy = decode_buy(sig)
            if not buy:
                continue

            # Enriquecer con DexScreener
            meta = enrich_token(buy["token_mint"], buy["ts"])

            row = {
                "wallet":       wallet,
                "wallet_label": label,
                "tx_sig":       sig,
                "ts":           buy["ts"],
                "token_mint":   buy["token_mint"],
                "program":      buy["program"],
                "sol_spent":    buy["sol_spent"],
                **{k: meta.get(k) for k in [
                    "token_symbol", "price_usd", "mcap_usd", "liquidity_usd",
                    "token_age_min", "pair_created_at",
                    "price_change_5m", "price_change_1h",
                    "buys_5m", "sells_5m",
                ]},
                "sell_ts":  None,
                "hold_min": None,
                "pnl_pct":  None,
                "outcome":  "UNKNOWN",
            }

            cursor.execute("""
                INSERT OR IGNORE INTO trades
                (wallet, wallet_label, tx_sig, ts, token_mint, token_symbol, program,
                 sol_spent, price_usd, mcap_usd, liquidity_usd, token_age_min,
                 pair_created_at, price_change_5m, price_change_1h, buys_5m, sells_5m,
                 sell_ts, hold_min, pnl_pct, outcome)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                row["wallet"], row["wallet_label"], row["tx_sig"], row["ts"],
                row["token_mint"], row.get("token_symbol"), row["program"],
                row["sol_spent"], row.get("price_usd"), row.get("mcap_usd"),
                row.get("liquidity_usd"), row.get("token_age_min"),
                row.get("pair_created_at"), row.get("price_change_5m"),
                row.get("price_change_1h"), row.get("buys_5m"), row.get("sells_5m"),
                None, None, None, "UNKNOWN",
            ))
            conn.commit()
            buys_found += 1
            total_inserted += 1

            time.sleep(0.05)

        log.info(f"  ✅ {buys_found} compras guardadas para {label}")

    conn.close()
    log.info(f"\n{'═'*50}")
    log.info(f"Recolección completa — {total_inserted} trades nuevos en {db_path}")
    return total_inserted


if __name__ == "__main__":
    collect()

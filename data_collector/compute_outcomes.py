"""
Segundo pase sobre wallet_history.db: detecta ventas y calcula outcomes.

Para cada compra con outcome=UNKNOWN, descarga las transacciones más recientes
de la wallet, busca la venta del mismo token y calcula P&L real.

Actualiza las columnas: sell_ts, hold_min, pnl_pct, outcome (WIN/LOSS).

Uso:
    python3 data_collector/compute_outcomes.py
"""

import os
import sqlite3
import sys
import time
from collections import defaultdict

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.logger import get_logger

log = get_logger("compute_outcomes")

RPC_HTTP    = os.getenv("SOLANA_RPC_HTTP", "https://api.mainnet-beta.solana.com")
DB_PATH     = "data/wallet_history.db"
SIGS_LIMIT  = 1000  # mismo cap que fetch_history
SELL_WINDOW = 200   # máximo de txs recientes a revisar por compra buscando venta

_client = httpx.Client(timeout=20)


def _rpc(method: str, params: list):
    for attempt in range(3):
        try:
            r = _client.post(RPC_HTTP, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params
            })
            data = r.json()
            if "error" in data:
                return None
            return data.get("result")
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None


def get_signatures(wallet: str) -> list[str]:
    """Devuelve hasta SIGS_LIMIT firmas recientes (más nuevo primero)."""
    sigs = []
    before = None
    while len(sigs) < SIGS_LIMIT:
        params: list = [wallet, {"limit": 1000, "commitment": "confirmed"}]
        if before:
            params[1]["before"] = before
        result = _rpc("getSignaturesForAddress", params)
        if not result:
            break
        batch = [item["signature"] for item in result if not item.get("err")]
        sigs.extend(batch)
        if len(result) < 1000:
            break
        before = result[-1]["signature"]
        time.sleep(0.2)
    return sigs[:SIGS_LIMIT]


def decode_tx(sig: str):
    """Decodifica tx y retorna dict con type BUY/SELL, token_mint y sol_delta."""
    SWAP_PROGRAMS = {
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
        "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
    }
    SOL_MINT = "So11111111111111111111111111111111111111112"

    result = _rpc("getTransaction", [
        sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}
    ])
    if not result:
        return None

    meta     = result.get("meta") or {}
    tx_data  = result.get("transaction") or {}
    msg      = tx_data.get("message") or {}
    btime    = result.get("blockTime", 0)

    if meta.get("err"):
        return None

    accounts = [a.get("pubkey", a) if isinstance(a, dict) else a
                for a in (msg.get("accountKeys") or [])]

    if not any(p in accounts for p in SWAP_PROGRAMS):
        return None

    pre_bal  = (meta.get("preBalances")  or [0])[0]
    post_bal = (meta.get("postBalances") or [0])[0]
    sol_delta = (pre_bal - post_bal) / 1e9  # >0 = SOL salió (compra), <0 = SOL entró (venta)

    pre_tok  = {t["accountIndex"]: t for t in (meta.get("preTokenBalances")  or [])}
    post_tok = {t["accountIndex"]: t for t in (meta.get("postTokenBalances") or [])}

    best_mint  = None
    best_delta = 0.0

    all_idxs = set(pre_tok) | set(post_tok)
    for idx in all_idxs:
        mint = (post_tok.get(idx) or pre_tok.get(idx) or {}).get("mint", "")
        if not mint or mint == SOL_MINT:
            continue
        pre_amt  = float(((pre_tok.get(idx)  or {}).get("uiTokenAmount") or {}).get("uiAmount") or 0)
        post_amt = float(((post_tok.get(idx) or {}).get("uiTokenAmount") or {}).get("uiAmount") or 0)
        delta = post_amt - pre_amt
        if abs(delta) > abs(best_delta):
            best_delta = delta
            best_mint  = mint

    if not best_mint:
        return None

    tx_type = None
    if sol_delta > 0.001 and best_delta > 0:
        tx_type = "BUY"
    elif sol_delta < -0.001 and best_delta < 0:
        tx_type = "SELL"

    if not tx_type:
        return None

    return {
        "sig":        sig,
        "ts":         btime,
        "type":       tx_type,
        "token_mint": best_mint,
        "sol_amount": abs(sol_delta),
    }


def process_wallet(conn, wallet: str, label: str):
    cursor = conn.cursor()

    # Cargar buys UNKNOWN de esta wallet
    cursor.execute("""
        SELECT id, tx_sig, ts, token_mint, sol_spent
        FROM trades
        WHERE wallet = ? AND outcome = 'UNKNOWN'
        ORDER BY ts DESC
    """, (wallet,))
    unknown = cursor.fetchall()

    if not unknown:
        log.info(f"  {label}: sin trades UNKNOWN, saltando")
        return 0

    log.info(f"  {label}: {len(unknown)} trades UNKNOWN — descargando sigs...")

    sigs = get_signatures(wallet)
    log.info(f"  {label}: {len(sigs)} sigs descargadas")

    # Construir índice: sig → posición (0 = más reciente)
    sig_idx = {s: i for i, s in enumerate(sigs)}

    # Decodificar todas las txs una vez y clasificar
    log.info(f"  {label}: decodificando {len(sigs)} txs...")
    decoded: dict[str, dict] = {}   # sig → decoded_tx
    for i, sig in enumerate(sigs):
        tx = decode_tx(sig)
        if tx:
            decoded[sig] = tx
        time.sleep(0.05)
        if (i + 1) % 100 == 0:
            log.info(f"    {i+1}/{len(sigs)} decodificadas ({len(decoded)} swaps)")

    # Agrupar decoded txs por token_mint → lista ordenada por ts
    sells_by_token: dict[str, list[dict]] = defaultdict(list)
    for tx in decoded.values():
        if tx["type"] == "SELL":
            sells_by_token[tx["token_mint"]].append(tx)
    for lst in sells_by_token.values():
        lst.sort(key=lambda x: x["ts"])

    # Calcular outcomes
    updated = 0
    for row_id, buy_sig, buy_ts, token_mint, sol_spent in unknown:
        sells = sells_by_token.get(token_mint, [])
        # Buscar la primera venta DESPUÉS del buy
        for sell in sells:
            if sell["ts"] > buy_ts:
                pnl_pct  = round((sell["sol_amount"] / sol_spent - 1) * 100, 2) if sol_spent else 0
                hold_min = round((sell["ts"] - buy_ts) / 60, 1)
                outcome  = "WIN" if pnl_pct > 0 else "LOSS"
                cursor.execute("""
                    UPDATE trades
                    SET sell_ts=?, hold_min=?, pnl_pct=?, outcome=?
                    WHERE id=?
                """, (sell["ts"], hold_min, pnl_pct, outcome, row_id))
                conn.commit()
                updated += 1
                break

    log.info(f"  ✅ {label}: {updated}/{len(unknown)} outcomes calculados")
    return updated


def main():
    if not os.path.exists(DB_PATH):
        log.error(f"DB no encontrada: {DB_PATH}. Corre fetch_history.py primero.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cursor = conn.cursor()
    cursor.execute("""
        SELECT wallet, wallet_label, COUNT(*) as n
        FROM trades WHERE outcome='UNKNOWN'
        GROUP BY wallet, wallet_label
    """)
    wallets = cursor.fetchall()

    log.info(f"Wallets con trades UNKNOWN: {len(wallets)}")
    for w in wallets:
        log.info(f"  {w['wallet_label']}: {w['n']} trades")

    total = 0
    for w in wallets:
        log.info(f"\n{'─'*50}")
        total += process_wallet(conn, w["wallet"], w["wallet_label"])
        time.sleep(3)  # evitar rate limit de Helius entre wallets

    conn.close()

    log.info(f"\n{'═'*50}")
    log.info(f"Total outcomes calculados: {total}")

    # Resumen final
    conn2 = sqlite3.connect(DB_PATH)
    c2 = conn2.cursor()
    c2.execute("SELECT outcome, COUNT(*) FROM trades GROUP BY outcome")
    for row in c2.fetchall():
        log.info(f"  {row[0]}: {row[1]}")
    conn2.close()


if __name__ == "__main__":
    main()

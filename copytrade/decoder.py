"""
Decodifica transacciones de Solana para detectar swaps.
Soporta Jupiter v6, Raydium AMM, Orca Whirlpool.
"""

from config import SWAP_PROGRAMS, TOKENS
from utils.logger import get_logger
log = get_logger("decoder")


# Reverse lookup: mint → symbol
MINT_TO_SYMBOL = {v: k for k, v in TOKENS.items()}


def detect_swap(tx: dict) -> dict | None:
    """
    Analiza una transacción confirmada y extrae el swap si existe.

    Retorna:
        {
          "wallet":      str,      # wallet que hizo el swap
          "program":     str,      # Jupiter / Raydium / Orca
          "token_in":    str,      # mint del token vendido
          "token_out":   str,      # mint del token comprado
          "symbol_in":   str,
          "symbol_out":  str,
          "amount_in":   int,      # en unidades mínimas
          "amount_out":  int,
        }
    o None si no es un swap.
    """
    try:
        meta = tx.get("meta", {})
        if meta.get("err"):
            return None

        msg          = tx.get("transaction", {}).get("message", {})
        loaded       = meta.get("loadedAddresses", {})
        account_keys = (
            msg.get("accountKeys", [])
            + loaded.get("writable", [])
            + loaded.get("readonly", [])
        )
        instructions = msg.get("instructions", [])

        # Detectar si alguna instrucción toca un programa de swap
        program_hit = None
        for ix in instructions:
            prog_idx = ix.get("programIdIndex", -1)
            if 0 <= prog_idx < len(account_keys):
                prog = account_keys[prog_idx]
                if prog in SWAP_PROGRAMS:
                    program_hit = prog
                    break

        # Buscar en innerInstructions también
        if not program_hit:
            for inner in meta.get("innerInstructions", []):
                for ix in inner.get("instructions", []):
                    prog_idx = ix.get("programIdIndex", -1)
                    if 0 <= prog_idx < len(account_keys):
                        prog = account_keys[prog_idx]
                        if prog in SWAP_PROGRAMS:
                            program_hit = prog
                            break

        if not program_hit:
            # Log todos los programas únicos de esta tx para diagnóstico
            seen = set()
            for ix in instructions:
                idx = ix.get("programIdIndex", -1)
                if 0 <= idx < len(account_keys):
                    seen.add(account_keys[idx])
            for inner in meta.get("innerInstructions", []):
                for ix in inner.get("instructions", []):
                    idx = ix.get("programIdIndex", -1)
                    if 0 <= idx < len(account_keys):
                        seen.add(account_keys[idx])
            log.info(f"TX sin swap — programas: {', '.join(list(seen)[:5])}")
            return None

        # Analizar cambios de balance de tokens para saber qué se compró/vendió
        pre_balances  = {b["accountIndex"]: b for b in meta.get("preTokenBalances",  [])}
        post_balances = {b["accountIndex"]: b for b in meta.get("postTokenBalances", [])}

        deltas = []
        all_idxs = set(pre_balances) | set(post_balances)
        for idx in all_idxs:
            pre  = int(pre_balances.get(idx,  {}).get("uiTokenAmount", {}).get("amount", 0))
            post = int(post_balances.get(idx, {}).get("uiTokenAmount", {}).get("amount", 0))
            diff = post - pre
            if diff == 0:
                continue
            mint  = (post_balances.get(idx) or pre_balances.get(idx, {})).get("mint", "")
            owner = (post_balances.get(idx) or pre_balances.get(idx, {})).get("owner", "")
            deltas.append({"mint": mint, "owner": owner, "delta": diff})

        # Filtrar por la wallet objetivo (account_keys[0] = fee payer = signer)
        wallet = account_keys[0] if account_keys else ""
        sold   = [d for d in deltas if d["delta"] < 0 and d["owner"] == wallet]
        bought = [d for d in deltas if d["delta"] > 0 and d["owner"] == wallet]

        # Pump.fun usa SOL nativo — no aparece en token balances, sino en preBalances/postBalances
        SOL_MINT = "So11111111111111111111111111111111111111112"
        SOL_MIN_LAMPORTS = 5_000_000  # 0.005 SOL mínimo
        pre_sol  = meta.get("preBalances",  [])
        post_sol = meta.get("postBalances", [])
        if pre_sol and post_sol:
            sol_delta = post_sol[0] - pre_sol[0]
            if not sold and sol_delta < -SOL_MIN_LAMPORTS:
                # Wallet gastó SOL → token_in = SOL
                sold = [{"mint": SOL_MINT, "delta": sol_delta, "owner": wallet}]
            elif not bought and sol_delta > SOL_MIN_LAMPORTS:
                # Wallet recibió SOL → token_out = SOL
                bought = [{"mint": SOL_MINT, "delta": sol_delta, "owner": wallet}]

        if not sold or not bought:
            return None

        # Tomamos el mayor movimiento de cada lado
        token_in_info  = min(sold,   key=lambda x: x["delta"])
        token_out_info = max(bought, key=lambda x: x["delta"])

        wallet = account_keys[0] if account_keys else "unknown"

        prog_name = {
            "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter",
            "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium",
            "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc":  "Orca",
            "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
            "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":  "Pump.fun",
            "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA":  "PumpSwap",
            "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora",
        }.get(program_hit, program_hit[:8])

        mint_in  = token_in_info["mint"]
        mint_out = token_out_info["mint"]

        return {
            "wallet":     wallet,
            "program":    prog_name,
            "token_in":   mint_in,
            "token_out":  mint_out,
            "symbol_in":  MINT_TO_SYMBOL.get(mint_in,  mint_in[:6]),
            "symbol_out": MINT_TO_SYMBOL.get(mint_out, mint_out[:6]),
            "amount_in":  abs(token_in_info["delta"]),
            "amount_out": token_out_info["delta"],
        }

    except Exception:
        return None

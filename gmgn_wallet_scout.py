#!/usr/bin/env python3
"""
Script manual (no corre en el loop del bot ni en launchd) para descubrir
wallets candidatas nuevas vía el smart money de GMGN, cruzando contra las
ya conocidas en config.WALLET_LABELS. No escribe en config.py — solo
imprime un reporte para que el usuario decida a mano si las agrega en
fase SIM (mismo flujo manual de siempre).

Uso: python gmgn_wallet_scout.py [--chain sol] [--limit 50]
"""
import argparse

from copytrade.gmgn_client import get_smart_money_wallets
from utils.logger import get_logger

log = get_logger("gmgn_wallet_scout")


def find_new_candidates(smart_money: list[dict], known_addresses: set[str]) -> list[dict]:
    """Filtra smart_money (lista de dicts con al menos 'address') excluyendo
    las que ya están en known_addresses. Preserva el orden de entrada."""
    return [w for w in smart_money if w.get("address") not in known_addresses]


def _print_report(candidates: list[dict]) -> None:
    if not candidates:
        print("Sin candidatas nuevas.")
        return
    print(f"{'Dirección':<46} {'Win rate':>10} {'PnL':>12} {'Clasificación':<15}")
    for c in candidates:
        addr = c.get("address", "?")
        wr = c.get("win_rate")
        pnl = c.get("pnl_usd")
        tag = c.get("tag", "?")
        wr_s = f"{wr:.1f}%" if wr is not None else "?"
        pnl_s = f"${pnl:,.0f}" if pnl is not None else "?"
        print(f"{addr:<46} {wr_s:>10} {pnl_s:>12} {tag:<15}")


def main() -> None:
    import config

    parser = argparse.ArgumentParser(description="Descubre wallets candidatas nuevas vía GMGN smart money.")
    parser.add_argument("--chain", default="sol")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    smart_money = get_smart_money_wallets(chain=args.chain, limit=args.limit)
    known = set(config.WALLET_LABELS.keys())
    candidates = find_new_candidates(smart_money, known)
    log.info(f"[gmgn_wallet_scout] {len(smart_money)} wallets smart money, {len(candidates)} nuevas")
    _print_report(candidates)


if __name__ == "__main__":
    main()

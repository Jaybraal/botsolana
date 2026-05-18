#!/usr/bin/env python3
"""
ANÁLISIS DE EXECUTION DRIFT — SIM vs LIVE
Lee data/execution_drift.jsonl y muestra:
  - Performance del SIM (baseline)
  - Performance LIVE real (cuando exista)
  - Drift entre ambos (cuánto del edge sobrevive on-chain)

Uso:
  python3 analyze_drift.py
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

DRIFT_FILE = Path("data/execution_drift.jsonl")


def load_entries():
    if not DRIFT_FILE.exists():
        print("No hay datos aún. El bot debe correr al menos un ciclo para generar execution_drift.jsonl")
        sys.exit(0)
    entries = []
    with open(DRIFT_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    return entries


def stats_block(entries: list, label: str):
    if not entries:
        print(f"\n  [{label}] Sin datos aún.")
        return

    total  = len(entries)
    wins   = [e for e in entries if e["real_pnl_sol"] > 0]
    losses = [e for e in entries if e["real_pnl_sol"] <= 0]

    total_spent    = sum(e["sol_spent_real_sol"]    for e in entries)
    total_received = sum(e["sol_received_real_sol"] for e in entries)
    total_pnl      = sum(e["real_pnl_sol"]          for e in entries)
    avg_pnl_pct    = sum(e["real_pnl_pct"]          for e in entries) / total
    avg_latency    = sum(e.get("buy_latency_ms", 0) for e in entries) / total
    avg_hold       = sum(e["hold_min"]              for e in entries) / total

    roi = (total_pnl / total_spent * 100) if total_spent > 0 else 0.0

    print(f"\n  ── {label} ──")
    print(f"  Trades:             {total}")
    print(f"  Win rate:           {len(wins)/total*100:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  SOL gastado:        {total_spent:.5f}")
    print(f"  SOL recibido:       {total_received:.5f}")
    print(f"  P&L neto:           {total_pnl:+.5f} SOL")
    print(f"  ROI acumulado:      {roi:+.1f}%")
    print(f"  Avg P&L por trade:  {avg_pnl_pct:+.1f}%")
    print(f"  Avg latencia buy:   {avg_latency:.0f}ms")
    print(f"  Avg hold time:      {avg_hold:.1f}min")

    # Por wallet
    by_wallet = defaultdict(list)
    for e in entries:
        by_wallet[e["wallet_label"]].append(e)

    print(f"\n  Wallet{'':8} | Trades | WR    | P&L SOL  | Lat ms | Hold")
    print("  " + "-"*58)
    for wallet, trades in sorted(by_wallet.items(), key=lambda x: -sum(t["real_pnl_sol"] for t in x[1])):
        n   = len(trades)
        w   = sum(1 for t in trades if t["real_pnl_sol"] > 0)
        pnl = sum(t["real_pnl_sol"] for t in trades)
        lat = sum(t.get("buy_latency_ms", 0) for t in trades) / n
        hld = sum(t["hold_min"] for t in trades) / n
        print(f"  {wallet:14} | {n:6} | {w/n*100:4.0f}% | {pnl:+.5f} | {lat:6.0f} | {hld:.1f}m")


def main():
    entries = load_entries()
    if not entries:
        print("Archivo vacío — sin trades cerrados aún.")
        return

    sim_entries  = [e for e in entries if e.get("mode") == "sim"]
    live_entries = [e for e in entries if e.get("mode") != "sim"]

    print("\n" + "="*62)
    print("  EXECUTION DRIFT REPORT — SIM vs LIVE REAL")
    print("="*62)

    stats_block(sim_entries,  "SIM  (baseline)")
    stats_block(live_entries, "LIVE (on-chain)")

    # Comparación directa si hay datos de ambos
    if sim_entries and live_entries:
        sim_wr   = sum(1 for e in sim_entries  if e["real_pnl_sol"] > 0) / len(sim_entries)  * 100
        live_wr  = sum(1 for e in live_entries if e["real_pnl_sol"] > 0) / len(live_entries) * 100
        sim_avg  = sum(e["real_pnl_pct"] for e in sim_entries)  / len(sim_entries)
        live_avg = sum(e["real_pnl_pct"] for e in live_entries) / len(live_entries)
        print("\n  ── DRIFT ──")
        print(f"  Win rate drift:   {live_wr - sim_wr:+.1f}pp  (SIM {sim_wr:.1f}% → LIVE {live_wr:.1f}%)")
        print(f"  Avg P&L drift:    {live_avg - sim_avg:+.1f}pp  (SIM {sim_avg:+.1f}% → LIVE {live_avg:+.1f}%)")
        edge_survival = (live_avg / sim_avg * 100) if sim_avg != 0 else 0
        print(f"  Edge survival:    {edge_survival:.0f}% del P&L del SIM sobrevive on-chain")

    # Últimos 10 trades (todos los modos)
    print(f"\n  Últimos 10 trades")
    print(f"  {'Modo':5} {'Símbolo':10} {'Wallet':12} {'P&L%':>8} {'Hold':>6} {'Lat ms':>7}")
    print("  " + "-"*56)
    for e in entries[-10:]:
        modo = e.get("mode", "live").upper()[:4]
        print(
            f"  {modo:5} {e['symbol']:10} {e['wallet_label']:12} "
            f"{e['real_pnl_pct']:>+8.1f}% {e['hold_min']:>5.1f}m {e.get('buy_latency_ms', 0):>7.0f}"
        )

    print("\n" + "="*62 + "\n")


if __name__ == "__main__":
    main()

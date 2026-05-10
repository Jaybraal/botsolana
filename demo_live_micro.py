#!/usr/bin/env python3
"""
DEMO LIVE MICRO — Simula 48 horas de micro-live trading
Emite trades en tiempo real a JSONL, como si fuera live
"""

import json
import random
import time
import os
from datetime import datetime, timedelta
from pathlib import Path

from utils.logger import get_logger
from utils.exit_degradation import exit_sim
from config import WALLET_LABELS

log = get_logger("demo-live-micro")

# Config
INITIAL_CAPITAL = 50.0  # USD
DEMO_DURATION_HOURS = 48
TRADES_PER_HOUR = 22  # histórico
DEMO_SPEED = 10  # 1 trade cada 10 segundos (acelerado)

WALLET_WEIGHTS = {
    "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk": 0.40,  # Cupsey-2
    "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9": 0.30,  # Decu
    "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o": 0.20,  # Cented
    "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f": 0.10,  # Cupsey
}

WALLET_PERFORMANCE = {
    "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk": {  # Cupsey-2
        "historical_win_rate": 0.615,
        "avg_win_pct": 18.0,
        "avg_loss_pct": -8.0,
    },
    "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9": {  # Decu
        "historical_win_rate": 0.562,
        "avg_win_pct": 16.0,
        "avg_loss_pct": -7.5,
    },
    "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o": {  # Cented
        "historical_win_rate": 0.444,
        "avg_win_pct": 12.0,
        "avg_loss_pct": -6.0,
    },
    "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f": {  # Cupsey
        "historical_win_rate": 0.250,
        "avg_win_pct": 10.0,
        "avg_loss_pct": -5.0,
    },
}


def simulate_trade(wallet_pubkey: str, balance: float) -> dict:
    """Simula un trade individual"""

    weight = WALLET_WEIGHTS[wallet_pubkey]
    trade_size = balance * 0.035 * weight  # 3.5% sizing

    perf = WALLET_PERFORMANCE[wallet_pubkey]
    is_win = random.random() < perf["historical_win_rate"]

    if is_win:
        base_pnl = random.gauss(perf["avg_win_pct"], 5.0)
    else:
        base_pnl = random.gauss(perf["avg_loss_pct"], 2.0)

    # Exit degradation — realista
    exit_scenario = exit_sim.simulate_exit(
        token=f"{wallet_pubkey[:8]}_token",
        wanted_exit_pct=100,
        token_age_hours=random.uniform(2.0, 24.0),
        volume_trend_1h=random.gauss(15, 25),
        pool_liquidity_usd=random.uniform(500, 50000),
        mcap_usd=random.uniform(100000, 10000000),
    )

    # Apply exit degradation
    if exit_scenario['fail_reason']:
        effective_pnl = base_pnl * exit_scenario['loss_factor'] - exit_scenario['slippage'] * 100
    else:
        slippage_cost = random.uniform(1.5, 3.0)
        effective_pnl = base_pnl - slippage_cost

    pnl_usd = trade_size * effective_pnl / 100
    new_balance = max(0.5, balance + pnl_usd)

    wallet_label = WALLET_LABELS.get(wallet_pubkey, wallet_pubkey[:8])

    return {
        'wallet_pubkey': wallet_pubkey,
        'wallet_label': wallet_label,
        'token': f"MEM{random.randint(1000, 9999)}",
        'side': random.choice(['buy', 'sell']),
        'trade_size_usd': trade_size,
        'pnl_pct': effective_pnl,
        'pnl_usd': pnl_usd,
        'new_balance': max(1.0, new_balance),
        'won': effective_pnl > 0,
    }


class DemoLiveMicro:
    """Demo de micro-live trading"""

    def __init__(self, capital: float = 50.0):
        self.balance = capital
        self.initial_balance = capital
        self.trades = []
        self.session_start = datetime.now()
        self.log_file = "data/live_micro_session_demo.jsonl"
        self.emergency_stop = False

        os.makedirs("data", exist_ok=True)

        # Limpiar archivo anterior
        if os.path.exists(self.log_file):
            os.remove(self.log_file)

        log.info(f"🚀 DEMO LIVE INICIADA")
        log.info(f"   Capital: ${capital:.2f}")
        log.info(f"   Duración simulada: {DEMO_DURATION_HOURS}h (acelerada {DEMO_SPEED}s/trade)")
        log.info(f"   Logs en: {self.log_file}")

    def log_trade(self, trade_result: dict):
        """Registra un trade a JSONL"""

        record = {
            "timestamp": datetime.now().isoformat(),
            "wallet": trade_result['wallet_label'],
            "token": trade_result['token'],
            "side": trade_result['side'],
            "amount_usd": trade_result['trade_size_usd'],
            "pnl_pct": trade_result['pnl_pct'],
            "pnl_usd": trade_result['pnl_usd'],
            "status": "success",
            "balance": self.balance,
        }

        self.trades.append(record)

        # Persist to JSONL
        with open(self.log_file, "a") as f:
            f.write(json.dumps(record) + "\n")

        # Log
        emoji = "✅" if trade_result['won'] else "❌"
        log.info(
            f"{emoji} {trade_result['wallet_label']} {trade_result['side'].upper()} "
            f"{trade_result['token']} | {trade_result['pnl_pct']:+.1f}% "
            f"(${trade_result['pnl_usd']:+.2f}) | balance=${self.balance:.2f}"
        )

    def run_demo(self):
        """Corre la demo emitiendo trades en tiempo real"""

        total_trades = DEMO_DURATION_HOURS * TRADES_PER_HOUR

        print("\n" + "=" * 100)
        print("🎬 DEMO LIVE MICRO-TRADING — 48 HORAS SIMULADAS")
        print("=" * 100)
        print(f"\nEn otra terminal, monitorea con:")
        print(f"  tail -f {self.log_file} | jq '.'")
        print(f"\nEsta demo emite 1 trade cada {DEMO_SPEED}s (~{total_trades * DEMO_SPEED / 60 / 60:.1f}h wall-clock)")
        print("=" * 100 + "\n")

        for i in range(total_trades):
            # Selecciona wallet con probabilidad proporcional a peso
            wallet_pubkey = random.choices(
                list(WALLET_WEIGHTS.keys()),
                weights=list(WALLET_WEIGHTS.values()),
                k=1
            )[0]

            # Simula trade
            trade_result = simulate_trade(wallet_pubkey, self.balance)
            self.balance = trade_result['new_balance']

            # Log
            self.log_trade(trade_result)

            # Progress cada 50 trades
            if (i + 1) % 50 == 0:
                elapsed = (datetime.now() - self.session_start).total_seconds() / 60
                print(
                    f"[{i+1:4d}/{total_trades}] | "
                    f"Balance: ${self.balance:.2f} | "
                    f"ROI: {(self.balance/self.initial_balance - 1)*100:+.1f}% | "
                    f"Elapsed: {elapsed:.0f}m"
                )

            # Sleep para simular en tiempo real
            time.sleep(DEMO_SPEED)

        # Resumen final
        self.print_summary()

    def print_summary(self):
        """Imprime resumen final"""

        elapsed = (datetime.now() - self.session_start).total_seconds() / 60
        roi = (self.balance / self.initial_balance - 1) * 100

        wins = sum(1 for t in self.trades if t.get("pnl_usd", 0) > 0)
        losses = sum(1 for t in self.trades if t.get("pnl_usd", 0) < 0)
        total = wins + losses

        print("\n" + "=" * 100)
        print("📊 DEMO LIVE SESSION SUMMARY")
        print("=" * 100)
        print(f"\nDuración real: {elapsed:.1f} minutos")
        print(f"Capital Inicial: ${self.initial_balance:.2f}")
        print(f"Balance Final: ${self.balance:.2f}")
        print(f"Ganancia/Pérdida: ${self.balance - self.initial_balance:+.2f}")
        print(f"ROI: {roi:+.1f}%")

        if total > 0:
            print(f"\nTrades: {total} | Wins: {wins} | Losses: {losses}")
            print(f"Win Rate: {wins/total*100:.1f}%")

        print("\n" + "=" * 100)
        print(f"✅ Logs guardados en: {self.log_file}")
        print("=" * 100 + "\n")

        # Export para comparación
        return {
            'trades': len(self.trades),
            'roi_pct': roi,
            'final_balance': self.balance,
            'win_rate': wins / total if total > 0 else 0,
        }


if __name__ == "__main__":
    random.seed(42)  # Para reproducibilidad

    demo = DemoLiveMicro(capital=50.0)
    demo.run_demo()

#!/usr/bin/env python3
"""
MICRO-LIVE TRADING: $20-50 reales

Características de seguridad:
- Capital máximo inicial: $100 (para testing)
- Circuit breaker: detiene si pierde >30% en 1 hora
- Emergency stop: CTRL+C mata todo inmediatamente
- Comparador live vs simulador en tiempo real
- Logging detallado de cada transacción
"""

import os
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from utils.logger import get_logger
from utils.wallet_scoring import scorer
from config import (
    WALLET_LABELS, WALLET_WEIGHTS, MAX_TRADE_PCT,
    PROPORTIONAL_MODE, STOP_LOSS_PCT
)

log = get_logger("live-micro")

MICRO_LIVE_CONFIG = {
    "max_initial_capital_usd": 100.0,
    "emergency_stop_loss_pct": 0.30,  # Detiene si pierdes >30%
    "emergency_stop_loss_hours": 1.0,  # En 1 hora
    "max_concurrent_positions": 3,
    "log_file": "data/live_micro_session.jsonl",
}


class MicroLiveTrader:
    """Trader con seguridad integrada para testing"""

    def __init__(self, initial_capital_usd: float):
        if initial_capital_usd > MICRO_LIVE_CONFIG["max_initial_capital_usd"]:
            raise ValueError(
                f"Capital máximo para micro-live: ${MICRO_LIVE_CONFIG['max_initial_capital_usd']}"
            )

        self.initial_capital = initial_capital_usd
        self.balance = initial_capital_usd
        self.trades = []
        self.positions = {}
        self.session_start = datetime.now()
        self.emergency_stop = False

        os.makedirs("data", exist_ok=True)

        log.info(f"🚀 MICRO-LIVE SESSION INICIADA")
        log.info(f"   Capital: ${initial_capital_usd:.2f}")
        log.info(f"   Max loss: ${initial_capital_usd * 0.30:.2f} en 1h")
        log.info(f"   Weighted allocation: {WALLET_WEIGHTS}")

    def check_emergency_stop(self) -> bool:
        """Verifica si se debe activar circuit breaker"""

        if self.emergency_stop:
            return True

        elapsed_hours = (datetime.now() - self.session_start).total_seconds() / 3600
        loss_pct = (self.balance - self.initial_capital) / self.initial_capital

        # Detener si perdió >30% en 1h
        if elapsed_hours <= 1.0 and loss_pct < -0.30:
            log.error(
                f"🚨 EMERGENCY STOP ACTIVATED: "
                f"Perdiste {abs(loss_pct)*100:.1f}% en {elapsed_hours:.2f}h"
            )
            self.emergency_stop = True
            return True

        return False

    def log_trade(
        self,
        wallet: str,
        token: str,
        side: str,  # "buy" o "sell"
        amount_usd: float,
        price: float,
        pnl_pct: float = None,
        status: str = "success"
    ):
        """Registra una transacción"""

        wallet_name = WALLET_LABELS.get(wallet, wallet[:8])
        trade_record = {
            "timestamp": datetime.now().isoformat(),
            "wallet": wallet_name,
            "token": token,
            "side": side,
            "amount_usd": amount_usd,
            "price": price,
            "pnl_pct": pnl_pct,
            "status": status,
            "balance": self.balance,
        }

        self.trades.append(trade_record)

        # Persist to JSONL
        with open(MICRO_LIVE_CONFIG["log_file"], "a") as f:
            f.write(json.dumps(trade_record) + "\n")

        # Log
        emoji = "✅" if pnl_pct is None or pnl_pct > 0 else "❌"
        log.info(
            f"{emoji} {wallet_name} {side.upper()} {token} @ ${price:.6f} "
            f"| ${amount_usd:.2f} | balance=${self.balance:.2f}"
        )

    def record_pnl(self, wallet: str, token: str, pnl_pct: float, pnl_usd: float):
        """Registra cierre de posición"""

        wallet_name = WALLET_LABELS.get(wallet, wallet[:8])
        self.balance += pnl_usd

        status = "win" if pnl_pct > 0 else "loss"
        emoji = "💰" if pnl_pct > 0 else "💔"

        log.info(
            f"{emoji} CLOSED {wallet_name} {token} "
            f"| {pnl_pct:+.1f}% (${pnl_usd:+.2f}) "
            f"| balance=${self.balance:.2f}"
        )

        scorer.record_trade(wallet, pnl_pct > 0, pnl_pct)

    def get_position_size(self, wallet: str) -> float:
        """Calcula tamaño de posición ponderado"""

        weight = WALLET_WEIGHTS.get(wallet, 0.1)
        base_size = self.balance * MAX_TRADE_PCT
        weighted_size = base_size * weight

        return weighted_size

    def print_summary(self):
        """Imprime resumen de sesión"""

        elapsed = (datetime.now() - self.session_start).total_seconds() / 60
        roi = (self.balance / self.initial_capital - 1) * 100

        wins = sum(1 for t in self.trades if t.get("pnl_pct", 0) > 0)
        losses = sum(1 for t in self.trades if t.get("pnl_pct", 0) < 0)
        total = wins + losses

        print("\n" + "=" * 80)
        print("📊 MICRO-LIVE SESSION SUMMARY")
        print("=" * 80)
        print(f"\nDuración: {elapsed:.1f} minutos")
        print(f"Capital Inicial: ${self.initial_capital:.2f}")
        print(f"Balance Actual: ${self.balance:.2f}")
        print(f"Ganancia/Pérdida: ${self.balance - self.initial_capital:+.2f}")
        print(f"ROI: {roi:+.1f}%")

        if total > 0:
            print(f"\nTrades: {total} | Wins: {wins} | Losses: {losses}")
            print(f"Win Rate: {wins/total*100:.1f}%")

        print(f"\nStatus: {'🚨 STOPPED' if self.emergency_stop else '✅ RUNNING'}")
        print("=" * 80 + "\n")

    def export_comparison_data(self):
        """Exporta datos para comparar live vs simulador"""

        comparison = {
            "session": {
                "start": self.session_start.isoformat(),
                "end": datetime.now().isoformat(),
                "duration_hours": (datetime.now() - self.session_start).total_seconds() / 3600,
                "initial_capital": self.initial_capital,
                "final_balance": self.balance,
                "roi_pct": (self.balance / self.initial_capital - 1) * 100,
            },
            "trades": self.trades,
            "wallet_stats": scorer.scores if hasattr(scorer, 'scores') else {},
        }

        filename = f"data/comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w") as f:
            json.dump(comparison, f, indent=2)

        log.info(f"✅ Comparison data exported: {filename}")
        return filename


def create_micro_live_session(capital_usd: float = 25.0) -> MicroLiveTrader:
    """Factory para crear sesión segura"""

    if capital_usd < 1.0:
        raise ValueError("Capital mínimo: $1.0")
    if capital_usd > 100.0:
        raise ValueError("Capital máximo para micro-live: $100.0")

    return MicroLiveTrader(capital_usd)


if __name__ == "__main__":
    # Test: crear sesión y mostrar resumen
    print("\n" + "=" * 80)
    print("MICRO-LIVE TRADER — Safety Harness")
    print("=" * 80)

    try:
        trader = create_micro_live_session(capital_usd=25.0)

        print(f"\n✅ Sesión creada: ${trader.initial_capital:.2f}")
        print(f"📊 Weighted allocation active: {WALLET_WEIGHTS}")
        print(f"⚠️  Circuit breaker: >30% loss en 1h = stop")
        print(f"\nLogs en: {MICRO_LIVE_CONFIG['log_file']}")

        trader.print_summary()
        print("\n👉 Listo para micro-live. Usa create_micro_live_session(capital) en otros modules.")

    except ValueError as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

#!/usr/bin/env python3
"""
COMPARADOR LIVE vs SIMULADOR

Monitorea una sesión micro-live y la compara contra:
1. Simulador Railway (datos históricos)
2. Simulador con fricción (nuevo)

Muestra divergencias en tiempo real.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from utils.logger import get_logger

log = get_logger("compare-live-sim")


class LiveVsSimComparator:
    """Compara performance de live trading vs simuladores"""

    def __init__(self, live_session_file: str):
        self.live_file = live_session_file
        self.live_trades = []
        self.live_stats = {}

        # Benchmarks
        self.railway_roi = 45325.3  # Histórico sin fricción
        self.realistic_roi = -31.4  # Con fricción
        self.expected_range = (500, 5000)  # Rango esperado realista

        self._load_live_session()

    def _load_live_session(self):
        """Carga trades en vivo"""

        if not os.path.exists(self.live_file):
            log.warning(f"Live session file not found: {self.live_file}")
            return

        trades = []
        try:
            with open(self.live_file) as f:
                for line in f:
                    try:
                        trades.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

            self.live_trades = trades
            log.info(f"✅ Cargados {len(trades)} trades en vivo")

        except Exception as e:
            log.error(f"Error loading live session: {e}")

    def calculate_live_stats(self) -> Dict:
        """Calcula stats de sesión en vivo"""

        if not self.live_trades:
            return {}

        # Extraer datos
        closes = [t for t in self.live_trades if "pnl_pct" in t and t["pnl_pct"] is not None]
        wins = sum(1 for t in closes if t["pnl_pct"] > 0)
        losses = sum(1 for t in closes if t["pnl_pct"] < 0)

        if not closes:
            return {"trades": len(self.live_trades), "closed_trades": 0}

        balances = [t["balance"] for t in self.live_trades if "balance" in t]
        initial_balance = balances[0] if balances else 20.0
        final_balance = balances[-1] if balances else 20.0

        roi = (final_balance / initial_balance - 1) * 100 if initial_balance > 0 else 0

        return {
            "total_trades": len(self.live_trades),
            "closed_trades": len(closes),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / len(closes) if closes else 0,
            "initial_balance": initial_balance,
            "final_balance": final_balance,
            "roi_pct": roi,
            "duration_hours": (
                (datetime.fromisoformat(closes[-1]["timestamp"]) -
                 datetime.fromisoformat(closes[0]["timestamp"])).total_seconds() / 3600
                if closes else 0
            ),
        }

    def print_comparison(self):
        """Imprime comparación lado a lado"""

        live_stats = self.calculate_live_stats()

        if not live_stats:
            log.warning("No live trades to compare")
            return

        live_roi = live_stats.get("roi_pct", 0)

        print("\n" + "=" * 100)
        print("📊 LIVE vs SIMULADOR COMPARISON")
        print("=" * 100)

        print(f"\n{'MÉTRICA':<30} {'RAILWAY (sin fricción)':<25} {'REALISTA (con fricción)':<25} {'LIVE':<15}")
        print("-" * 100)

        print(f"{'ROI':<30} {self.railway_roi:>23.1f}% {self.realistic_roi:>23.1f}% {live_roi:>13.1f}%")
        print(
            f"{'Capital Inicial':<30} ${20:<24.2f} ${20:<24.2f} "
            f"${live_stats.get('initial_balance', 0):<13.2f}"
        )
        print(
            f"{'Balance Final':<30} ${22712.66:<24.2f} ${13.73:<24.2f} "
            f"${live_stats.get('final_balance', 0):<13.2f}"
        )

        print(f"\n{'Trades Ejecutados':<30} {523:<25} {528:<25} {live_stats.get('closed_trades', 0):<15}")

        win_rate_live = live_stats.get("win_rate", 0) * 100
        print(f"{'Win Rate':<30} {56.0:<25.1f}% {42.0:<25.1f}% {win_rate_live:<14.1f}%")

        print("\n" + "=" * 100)

        # ANÁLISIS
        print("\n🔍 ANÁLISIS:")
        print("-" * 100)

        if live_roi > 0:
            if self.expected_range[0] <= live_roi <= self.expected_range[1]:
                print(f"✅ VIABLE: Live ROI ({live_roi:.1f}%) está en rango esperado ({self.expected_range[0]}-{self.expected_range[1]}%)")
            elif live_roi > self.expected_range[1]:
                print(f"⚠️  TOO GOOD: Live ROI ({live_roi:.1f}%) EXCEDE rango esperado. Check por optimism bias")
            else:
                print(f"🟡 MARGINAL: Live ROI ({live_roi:.1f}%) está ABAJO del rango esperado")
        else:
            print(f"❌ NEGATIVE: Live ROI ({live_roi:.1f}%) es NEGATIVO — edge no está confirmado")

        print(f"\n📈 Win Rate: {win_rate_live:.1f}%")
        if live_stats.get("win_rate", 0) > 0.50:
            print(f"   ✅ Arriba de 50% — sistema mostrando edge")
        else:
            print(f"   ❌ Debajo de 50% — sin edge confirmado")

        duration = live_stats.get("duration_hours", 0)
        if duration > 0:
            print(f"\n⏱️  Duración: {duration:.2f} horas")
            print(f"   Trades/hora: {live_stats.get('closed_trades', 0) / duration:.1f}")

        print("\n" + "=" * 100)

    def export_comparison(self) -> str:
        """Exporta reporte de comparación"""

        live_stats = self.calculate_live_stats()

        report = {
            "timestamp": datetime.now().isoformat(),
            "comparison": {
                "railway_roi_pct": self.railway_roi,
                "realistic_roi_pct": self.realistic_roi,
                "live_roi_pct": live_stats.get("roi_pct", 0),
                "live_win_rate": live_stats.get("win_rate", 0),
                "live_trades": live_stats.get("closed_trades", 0),
            },
            "status": self._get_status(live_stats),
        }

        filename = f"data/comparison_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w") as f:
            json.dump(report, f, indent=2)

        log.info(f"✅ Comparison report: {filename}")
        return filename

    def _get_status(self, live_stats: Dict) -> str:
        """Determina estado de validación"""

        roi = live_stats.get("roi_pct", 0)

        if roi > self.expected_range[1]:
            return "TOO_GOOD_CHECK_ASSUMPTIONS"
        elif roi >= self.expected_range[0]:
            return "VIABLE_CONFIRMED"
        elif roi > 0:
            return "MARGINAL_NEEDS_OPTIMIZATION"
        else:
            return "NEGATIVE_NEEDS_DEBUG"


def compare_session(session_file: str = None):
    """Compara sesión en vivo contra benchmarks"""

    if not session_file:
        session_file = "data/live_micro_session.jsonl"

    try:
        comparator = LiveVsSimComparator(session_file)
        comparator.print_comparison()
        comparator.export_comparison()

    except Exception as e:
        log.error(f"Error comparing: {e}")
        return False

    return True


if __name__ == "__main__":
    import sys

    session = sys.argv[1] if len(sys.argv) > 1 else None
    compare_session(session)

"""
Exit Degradation Simulator
Simula la realidad de salidas en tokens memecoin:
- Rugs: Solo puedes vender una fracción antes del collapse
- Dumps: Slippage y partial fills
- Panics: Liquidity drains
"""

import random
from datetime import datetime, timedelta

class ExitDegradation:
    """Simula fricción real en salidas"""

    def __init__(self):
        self.tx_history = {}  # Tracking de entradas por token

    def record_entry(self, token: str, amount_usd: float, timestamp: float = None):
        """Registra una entrada para tracking"""
        ts = timestamp or datetime.now().timestamp()
        if token not in self.tx_history:
            self.tx_history[token] = []
        self.tx_history[token].append({
            'time': ts,
            'amount': amount_usd,
            'type': 'entry'
        })

    def simulate_exit(
        self,
        token: str,
        wanted_exit_pct: float,  # 100 = quería vender todo
        token_age_hours: float,
        volume_trend_1h: float,  # -50 = bajó 50%, +100 = subió 100%
        pool_liquidity_usd: float,
        mcap_usd: float = None,
    ) -> dict:
        """
        Simula qué porcentaje realmente puedes salir

        Returns:
            {
                'actual_exit_pct': float,  # % que realmente pudiste vender
                'slippage': float,         # slippage en %
                'fail_reason': str,        # razón de degradación
                'loss_factor': float,      # 0.5 = perdiste 50% vs wanted
            }
        """

        # --- DETECCIÓN DE RUG PULL ---
        is_likely_rug = self._is_likely_rug(
            token_age_hours,
            volume_trend_1h,
            pool_liquidity_usd,
            mcap_usd
        )

        if is_likely_rug:
            return self._simulate_rug_exit(
                wanted_exit_pct,
                pool_liquidity_usd,
                token
            )

        # --- DETECCIÓN DE PANIC DUMP ---
        is_panic = volume_trend_1h < -30  # Volumen bajó >30%
        if is_panic:
            return self._simulate_panic_exit(wanted_exit_pct)

        # --- SALIDA NORMAL (con fricción estándar) ---
        return self._simulate_normal_exit(wanted_exit_pct)

    def _is_likely_rug(
        self,
        age_hours: float,
        vol_trend: float,
        liquidity: float,
        mcap: float
    ) -> bool:
        """Heurística para detectar probabilidad de rug"""

        # Token muy nuevo + volumen colapsando = HIGH RUG RISK
        if age_hours < 1 and vol_trend < -40:
            return True

        # Liquidity secándose rápido = RUG EN PROGRESO
        if age_hours < 2 and liquidity < 100:  # Menos de $100 de liquidity
            return True

        # MCap muy bajo + edad corta = alto riesgo
        if mcap and mcap < 50000 and age_hours < 3:
            return random.random() < 0.3  # 30% probabilidad

        return False

    def _simulate_rug_exit(
        self,
        wanted_exit_pct: float,
        pool_liquidity: float,
        token: str
    ) -> dict:
        """Simula una salida parcial en rug pull"""

        # En un rug, normalmente solo puedes vender 10-40% de lo que querías
        # porque la liquidez desaparece exponencialmente
        actual_pct = random.uniform(0.10, 0.40) * wanted_exit_pct

        # Slippage brutal: 40-70%
        slippage = random.uniform(0.40, 0.70)

        return {
            'actual_exit_pct': actual_pct,
            'slippage': slippage,
            'fail_reason': 'RUG_PULL',
            'loss_factor': actual_pct / wanted_exit_pct if wanted_exit_pct > 0 else 0,
        }

    def _simulate_panic_exit(self, wanted_exit_pct: float) -> dict:
        """Simula una salida en pánico (volume drop)"""

        # En pánico, logras vender 60-80% de lo que querías
        actual_pct = random.uniform(0.60, 0.80) * wanted_exit_pct

        # Slippage moderado: 8-20%
        slippage = random.uniform(0.08, 0.20)

        return {
            'actual_exit_pct': actual_pct,
            'slippage': slippage,
            'fail_reason': 'PANIC_DUMP',
            'loss_factor': actual_pct / wanted_exit_pct if wanted_exit_pct > 0 else 0,
        }

    def _simulate_normal_exit(self, wanted_exit_pct: float) -> dict:
        """Simula una salida normal (sin drama)"""

        # Generalmente logras vender 90-100% de lo que querías
        actual_pct = random.uniform(0.90, 1.00) * wanted_exit_pct

        # Slippage estándar: 1.5-4%
        slippage = random.uniform(0.015, 0.04)

        return {
            'actual_exit_pct': actual_pct,
            'slippage': slippage,
            'fail_reason': None,
            'loss_factor': actual_pct / wanted_exit_pct if wanted_exit_pct > 0 else 1.0,
        }

    def apply_exit_degradation(
        self,
        desired_pnl_pct: float,
        exit_scenario: dict
    ) -> float:
        """
        Aplica degradación al P&L deseado

        Si querías +30% pero solo pudiste vender el 40% a causa de rug:
        -> resultado real es mucho peor
        """

        actual_exit = exit_scenario['actual_exit_pct']
        slippage = exit_scenario['slippage']

        if actual_exit == 0:
            return -0.95  # Nearly total loss

        # Fórmula: solo capturas la ganancia en la parte que vendiste
        # + subes slippage cost
        effective_pnl = (desired_pnl_pct * actual_exit) - slippage

        return effective_pnl


# Singleton global
exit_sim = ExitDegradation()

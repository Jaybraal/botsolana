"""
Wallet Scoring & Dynamic Weighting
Calcula performance real de wallets copiadas y ajusta capital allocation
"""

import json
import os
from datetime import datetime, timedelta
from config import WALLET_LABELS, DYNAMIC_REWEIGHT, REWEIGHT_INTERVAL_HOURS

SCORES_FILE = "data/wallet_scores.json"


class WalletScorer:
    """Tracks performance y calcula pesos dinámicos"""

    def __init__(self):
        self.scores = self._load_scores()
        self.last_reweight = datetime.now()

    def _load_scores(self) -> dict:
        """Carga scores históricos"""
        if os.path.exists(SCORES_FILE):
            try:
                with open(SCORES_FILE) as f:
                    return json.load(f)
            except:
                pass
        return {}

    def _save_scores(self):
        """Persiste scores"""
        os.makedirs("data", exist_ok=True)
        with open(SCORES_FILE, "w") as f:
            json.dump(self.scores, f, indent=2)

    def record_trade(
        self,
        wallet: str,
        won: bool,
        pnl_pct: float,
        timestamp: float = None
    ):
        """Registra resultado de trade copiado"""

        ts = timestamp or datetime.now().timestamp()
        wallet_name = WALLET_LABELS.get(wallet, wallet[:8])

        if wallet_name not in self.scores:
            self.scores[wallet_name] = {
                'wins': 0,
                'losses': 0,
                'total_pnl_pct': 0.0,
                'trades': [],
                'last_update': ts,
            }

        score = self.scores[wallet_name]
        if won:
            score['wins'] += 1
        else:
            score['losses'] += 1

        score['total_pnl_pct'] += pnl_pct
        score['trades'].append({'won': won, 'pnl_pct': pnl_pct, 'time': ts})
        score['last_update'] = ts

        self._save_scores()

    def get_wallet_weight(self, wallet: str) -> float:
        """Retorna peso dinámico para una wallet (0.0-1.0)"""

        wallet_name = WALLET_LABELS.get(wallet, wallet[:8])

        if wallet_name not in self.scores:
            return 0.1  # Default bajo para wallets nuevas

        score = self.scores[wallet_name]
        total_trades = score['wins'] + score['losses']

        if total_trades == 0:
            return 0.1

        win_rate = score['wins'] / total_trades
        avg_pnl = score['total_pnl_pct'] / total_trades

        # Scoring: 60% por win rate, 40% por avg PnL
        performance = (win_rate * 0.60) + (max(0, avg_pnl / 100) * 0.40)

        # Normalizar a 0.1-1.0 range
        weight = max(0.05, min(1.0, performance))

        return weight

    def get_all_weights(self) -> dict:
        """Retorna dict de {wallet: weight} normalizado"""

        all_wallets = list(self.scores.keys())
        weights = {w: self.get_wallet_weight(w) for w in all_wallets}

        # Normalizar suma a 1.0
        total = sum(weights.values())
        if total > 0:
            weights = {w: v / total for w, v in weights.items()}

        return weights

    def should_reweight(self) -> bool:
        """Check si es hora de recalcular pesos"""
        if not DYNAMIC_REWEIGHT:
            return False

        elapsed = (datetime.now() - self.last_reweight).total_seconds() / 3600
        return elapsed >= REWEIGHT_INTERVAL_HOURS

    def reweight(self):
        """Recalcula pesos de todas las wallets"""
        if self.should_reweight():
            self.last_reweight = datetime.now()
            # Los pesos se recalculan automáticamente en get_all_weights()
            return True
        return False

    def get_wallet_stats(self, wallet: str) -> dict:
        """Retorna stats completos de una wallet"""

        wallet_name = WALLET_LABELS.get(wallet, wallet[:8])

        if wallet_name not in self.scores:
            return {'trades': 0, 'win_rate': 0, 'avg_pnl': 0, 'weight': 0.1}

        score = self.scores[wallet_name]
        total = score['wins'] + score['losses']

        return {
            'name': wallet_name,
            'trades': total,
            'wins': score['wins'],
            'losses': score['losses'],
            'win_rate': score['wins'] / total if total > 0 else 0,
            'total_pnl': score['total_pnl_pct'],
            'avg_pnl': score['total_pnl_pct'] / total if total > 0 else 0,
            'weight': self.get_wallet_weight(wallet),
        }

    def print_summary(self):
        """Imprime resumen de performance"""
        print("\n" + "=" * 80)
        print("📊 WALLET SCORING SUMMARY")
        print("=" * 80)

        wallets = list(self.scores.keys())
        if not wallets:
            print("No wallets tracked yet.")
            return

        # Sort by win rate
        sorted_wallets = sorted(
            wallets,
            key=lambda w: (self.scores[w]['wins'] / (self.scores[w]['wins'] + self.scores[w]['losses']))
            if (self.scores[w]['wins'] + self.scores[w]['losses']) > 0 else 0,
            reverse=True
        )

        for wallet in sorted_wallets:
            stats = self.get_wallet_stats(wallet)
            print(f"\n{wallet} [{stats['weight']:.1%} allocation]")
            print(f"  Trades: {stats['trades']} | Wins: {stats['wins']} | Losses: {stats['losses']}")
            print(f"  Win Rate: {stats['win_rate']:.1%} | Avg PnL: {stats['avg_pnl']:.2f}%")


# Singleton global
scorer = WalletScorer()

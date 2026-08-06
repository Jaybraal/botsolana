"""
Analiza el historial de trades por wallet para extraer features de
comportamiento (winrate, pnl promedio, timing de entrada respecto al pump)
que alimentan wallet_trust en root_config.Config como punto de partida —
no son reglas fijas, root_evolution.py sigue mutándolas libremente.
"""


def wallet_features(dataset: list[dict]) -> dict[str, dict]:
    by_wallet: dict[str, list[dict]] = {}
    for trade in dataset:
        by_wallet.setdefault(trade["wallet"], []).append(trade)

    out = {}
    for wallet, trades in by_wallet.items():
        n = len(trades)
        wins = sum(1 for t in trades if t.get("won"))
        pnl_values = [t.get("pnl_pct") or 0.0 for t in trades]
        change_values = [
            t["entry_context"].get("change_1h_pct")
            for t in trades
            if t.get("entry_context", {}).get("change_1h_pct") is not None
        ]
        out[wallet] = {
            "n_trades": n,
            "win_rate": round(wins / n * 100, 2) if n else 0.0,
            "avg_pnl_pct": round(sum(pnl_values) / n, 2) if n else 0.0,
            "avg_change_1h_pct_al_entrar": (
                round(sum(change_values) / len(change_values), 2) if change_values else None
            ),
        }
    return out


def initial_wallet_trust(dataset: list[dict], scale: float = 0.02) -> dict[str, float]:
    """(win_rate - 50) * scale — una wallet con el winrate promedio real
    medido (43.8%) arranca casi neutra; una mejor arranca con empujón
    positivo. Sigue siendo un punto de partida mutable, no un valor fijo."""
    features = wallet_features(dataset)
    return {w: round((f["win_rate"] - 50.0) * scale, 4) for w, f in features.items()}

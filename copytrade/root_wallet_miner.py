"""
Analiza el historial de trades por wallet para extraer features de
comportamiento (winrate, pnl promedio, timing de entrada respecto al pump)
que alimentan wallet_trust en root_config.Config como punto de partida —
no son reglas fijas, root_evolution.py sigue mutándolas libremente.
"""
from copytrade.gmgn_client import get_wallet_stats


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


def blend_with_gmgn(
    wallet_trust: dict[str, float], addresses: list[str], gmgn_weight: float = 0.5,
) -> dict[str, float]:
    """Mezcla el prior propio (wallet_trust, típicamente salido de
    initial_wallet_trust) con el winrate real de GMGN cuando hay datos
    disponibles para esa dirección. Si GMGN no tiene datos (sin API key,
    error de red, wallet desconocida), esa wallet queda con su valor
    original sin cambios — no se llama a la red desde initial_wallet_trust
    ni desde ningún otro punto del hot path, solo desde donde se arma el
    seed inicial. gmgn_weight pondera cuánto pesa GMGN frente al prior
    propio (0.5 = mitad y mitad; 1.0 = solo GMGN)."""
    out = dict(wallet_trust)
    for address in addresses:
        stats = get_wallet_stats(address)
        if not stats or stats.get("win_rate") is None:
            continue
        gmgn_trust = round((stats["win_rate"] - 50.0) * 0.02, 4)
        own_trust = out.get(address, 0.0)
        out[address] = round(own_trust * (1 - gmgn_weight) + gmgn_trust * gmgn_weight, 4)
    return out

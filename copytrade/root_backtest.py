"""
Backtest puro: dado un vector de config y un dataset de trades históricos
(formato de root_seed_loader / root_evolution), calcula qué pnl hubiera
dado esa config. Sobre trades con trayectoria completa puede re-simular
exits alternativos de verdad; sobre trades sin trayectoria (el historial
semilla) solo puede reevaluar si la config hubiera dicho COPIAR, usando el
pnl ya registrado — no inventa un exit que los datos no permiten calcular.
Sin I/O, sin red — todo en memoria, corre en milisegundos.
"""
from copytrade.root_config import Config, decide


def simulate_exit(
    entry_price: float, snapshots: list[list[float]], stop_loss_pct: float, max_hold_min: float,
) -> tuple[float, str]:
    """Recorre la trayectoria (elapsed_s, price) en orden y devuelve
    (pnl_pct, exit_reason) según el primer stop-loss o max-hold que se
    cumpla. Si ninguno se cumple durante toda la trayectoria conocida, sale
    al último precio con motivo 'end_of_data' — no inventa un precio futuro
    que no está en los datos."""
    if not snapshots:
        return 0.0, "sin_datos"
    last_pnl_pct = 0.0
    for elapsed_s, price in snapshots:
        if not price or price <= 0:
            continue
        pnl_pct = (price - entry_price) / entry_price * 100
        last_pnl_pct = pnl_pct
        if pnl_pct <= -stop_loss_pct:
            return pnl_pct, "stop_loss"
        if elapsed_s / 60.0 >= max_hold_min:
            return pnl_pct, "max_hold"
    return last_pnl_pct, "end_of_data"


def backtest_config(config: Config, dataset: list[dict]) -> dict:
    taken_pnls_usd: list[float] = []

    for trade in dataset:
        wallet = trade["wallet"]
        entry_context = trade["entry_context"]
        if decide(config, wallet, entry_context) != "COPIAR":
            continue

        if trade.get("trajectory"):
            pnl_pct, _reason = simulate_exit(
                trade["entry_price"], trade["trajectory"], config.stop_loss_pct, config.max_hold_min,
            )
        else:
            pnl_pct = trade.get("pnl_pct") or 0.0

        taken_pnls_usd.append(config.trade_usd * pnl_pct / 100.0)

    n_taken = len(taken_pnls_usd)
    pnl_total = sum(taken_pnls_usd)
    wins = sum(1 for p in taken_pnls_usd if p > 0)
    pnl_excluding_best = pnl_total - max(taken_pnls_usd) if taken_pnls_usd else 0.0

    return {
        "config_id": config.config_id,
        "n_taken": n_taken,
        "n_seen": len(dataset),
        "pnl_usd_total": round(pnl_total, 4),
        "win_rate": round(wins / n_taken * 100, 2) if n_taken else 0.0,
        "pnl_usd_excluding_best": round(pnl_excluding_best, 4),
    }

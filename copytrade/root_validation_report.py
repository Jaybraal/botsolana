"""Informe reproducible de la validación ROOT en paper trading.

Lee sólo operaciones ejecutables del modelo neto actual. No modifica balances,
configuraciones ni modos de ejecución.
"""
import json
from pathlib import Path

from copytrade.root_validation_pool import _eligible_history, _profit_factor


def summarize(config_id: str, history: list[dict]) -> dict:
    trades = _eligible_history(history)
    pnls = [float(trade["pnl_usd"]) for trade in trades]
    equity = peak = drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "config_id": config_id,
        "eligible_trades": len(trades),
        "net_pnl_usd": round(sum(pnls), 4),
        "profit_factor": round(_profit_factor(pnls), 4),
        "max_drawdown_usd": round(drawdown, 4),
        "eligible": len(trades) > 0,
    }


def build_report(data_dir: str | Path) -> dict:
    reports = []
    for path in sorted(Path(data_dir).glob("root_validation_*_history.json")):
        config_id = path.name.removeprefix("root_validation_").removesuffix("_history.json")
        with path.open() as file:
            history = [json.loads(line) for line in file if line.strip()]
        reports.append(summarize(config_id, history))
    return {
        "model": "root-v2-net-execution",
        "candidate_count": len(reports),
        "candidates": reports,
        "note": "Los registros sin liquidez verificable o sin costes netos no cuentan para promoción.",
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(build_report(root / "data"), indent=2, ensure_ascii=False))

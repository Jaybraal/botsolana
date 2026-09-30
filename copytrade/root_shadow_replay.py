"""Replay offline de decisiones ROOT, sin red ni órdenes.

Comprueba cuántas decisiones habrían pasado los filtros con señales ya
observadas. No calcula rentabilidad: no hay fills ni salidas.
"""
import json
from pathlib import Path

from copytrade.root_config import Config, decide


def replay(records: list[dict], config: Config) -> dict:
    decisions = []
    for record in records:
        context = record.get("entry_context") or {}
        decision = decide(config, record.get("wallet", "unknown"), context)
        try:
            executable = float(context.get("price_usd", 0)) > 0 and float(context.get("liquidity_usd", 0)) > 0
        except (TypeError, ValueError):
            executable = False
        decisions.append({"decision": decision, "executable": executable})
    copied = [item for item in decisions if item["decision"] == "COPIAR"]
    return {
        "signals": len(decisions), "copied": len(copied),
        "executable_copies": sum(item["executable"] for item in copied),
        "note": "Replay de decisiones; no es evidencia de rentabilidad ni ejecuta órdenes.",
    }


def load_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open() as file:
        return [json.loads(line) for line in file if line.strip()]


if __name__ == "__main__":
    from copytrade.root_decider import load_champion
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(replay(load_jsonl(root / "data/root_shadow_log.jsonl"), load_champion()), indent=2))

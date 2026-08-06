"""
Normaliza los historiales de trades existentes (el del bot de reglas viejo
y el propio de ROOT) a una forma común para que root_backtest.py y
root_wallet_miner.py no tengan que conocer los formatos de archivo
originales.

Por qué se usa el historial de reglas como semilla: son 1,219 resultados
reales de mercado y de wallets (ganó/perdió, contexto), no "lógica de
reglas" en sí — la lógica de reglas solo decidió CUÁLES de esos trades se
tomaron, lo cual sesga la muestra pero no invalida los resultados. Se
descartan los que no tienen entry_context: usar exit_context como si fuera
entry_context sería fuga de datos futuros (el bot no sabía eso al decidir).
"""
import json
import os


def _load_json_array(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def _load_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_rule_based_seed(path: str) -> list[dict]:
    """Normaliza data/sim_history.json (formato del bot de reglas: JSON
    array, wallet_label/token/entry_context/exit_context)."""
    out = []
    for t in _load_json_array(path):
        entry_context = t.get("entry_context") or {}
        if not entry_context:
            continue
        out.append({
            "wallet": t.get("wallet_label", "?"),
            "token_mint": t.get("token"),
            "entry_context": entry_context,
            "entry_price": t.get("entry_price"),
            "pnl_pct": t.get("pnl_pct"),
            "won": bool(t.get("won")),
            "trajectory": None,
        })
    return out


def load_root_sim_seed(path: str) -> list[dict]:
    """Normaliza data/root_sim_history.json (formato propio de ROOT: JSON
    Lines, wallet/token_mint/entry_context)."""
    out = []
    for t in _load_jsonl(path):
        entry_context = t.get("entry_context") or {}
        if not entry_context:
            continue
        out.append({
            "wallet": t.get("wallet", "?"),
            "token_mint": t.get("token_mint"),
            "entry_context": entry_context,
            "entry_price": t.get("entry_price"),
            "pnl_pct": t.get("pnl_pct"),
            "won": bool(t.get("won")),
            "trajectory": None,
        })
    return out


def load_seed_dataset(rule_history_path: str, root_sim_history_path: str) -> list[dict]:
    return load_rule_based_seed(rule_history_path) + load_root_sim_seed(root_sim_history_path)

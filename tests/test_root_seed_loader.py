import json

from copytrade.root_seed_loader import (
    load_rule_based_seed, load_root_sim_seed, load_seed_dataset,
)


def test_load_rule_based_seed_filtra_entry_context_vacio(tmp_path):
    path = tmp_path / "sim_history.json"
    path.write_text(json.dumps([
        {"wallet_label": "Theo", "token": "MINTA", "entry_context": {"mcap_usd": 5000},
         "pnl_pct": 12.0, "won": True},
        {"wallet_label": "Decu", "token": "MINTB", "entry_context": {},
         "exit_context": {"mcap_usd": 9000}, "pnl_pct": -5.0, "won": False},
    ]))
    records = load_rule_based_seed(str(path))
    assert len(records) == 1
    assert records[0]["wallet"] == "Theo"
    assert records[0]["token_mint"] == "MINTA"
    assert records[0]["entry_context"] == {"mcap_usd": 5000}
    assert records[0]["trajectory"] is None


def test_load_rule_based_seed_archivo_inexistente(tmp_path):
    assert load_rule_based_seed(str(tmp_path / "nope.json")) == []


def test_load_root_sim_seed_jsonl(tmp_path):
    path = tmp_path / "root_sim_history.json"
    lines = [
        json.dumps({"wallet": "Cupsey", "token_mint": "MINTC",
                    "entry_context": {"buy_pressure": 0.9}, "pnl_pct": 30.0, "won": True}),
        json.dumps({"wallet": "Yenni", "token_mint": "MINTD",
                    "entry_context": {}, "pnl_pct": -10.0, "won": False}),
    ]
    path.write_text("\n".join(lines) + "\n")
    records = load_root_sim_seed(str(path))
    assert len(records) == 1
    assert records[0]["wallet"] == "Cupsey"
    assert records[0]["token_mint"] == "MINTC"


def test_load_seed_dataset_junta_ambas_fuentes(tmp_path):
    rules_path = tmp_path / "sim_history.json"
    rules_path.write_text(json.dumps([
        {"wallet_label": "Theo", "token": "MINTA", "entry_context": {"mcap_usd": 1},
         "pnl_pct": 1.0, "won": True},
    ]))
    root_path = tmp_path / "root_sim_history.json"
    root_path.write_text(json.dumps(
        {"wallet": "Cupsey", "token_mint": "MINTC", "entry_context": {"mcap_usd": 2},
         "pnl_pct": 2.0, "won": True}
    ) + "\n")

    dataset = load_seed_dataset(str(rules_path), str(root_path))
    assert len(dataset) == 2
    assert {r["wallet"] for r in dataset} == {"Theo", "Cupsey"}

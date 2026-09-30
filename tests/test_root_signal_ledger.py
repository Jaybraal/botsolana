import json

import copytrade.root_signal_ledger as ledger


def test_record_signal_keeps_rejected_and_executable_context(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    record = ledger.record_signal("Theo", "MINT", {"price_usd": 1e-5, "liquidity_usd": 5000}, "SKIP", "champion")
    assert record["executable"] is True
    stored = json.loads((tmp_path / "ledger.jsonl").read_text())
    assert stored["decision"] == "SKIP"

#!/usr/bin/env python3
"""Envía el reporte diario de BotSolana por WhatsApp (vía baileys-server de CRM-auto)."""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

from copytrade.report import compute_summary, format_report_message

REPO_DIR = Path(__file__).resolve().parent
DATA_DIR = REPO_DIR / "data"
FAILURE_LOG = REPO_DIR / "logs" / "report_failures.log"
MAIN_PY = REPO_DIR / "main.py"

BAILEYS_URL = "http://localhost:3002/send"
SESSION_ID = "HzyDWOrkySUVdcoYUszi"
TO_NUMBER = "18295080887"


def load_json(path, default):
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def is_bot_alive():
    result = subprocess.run(["pgrep", "-f", str(MAIN_PY)], capture_output=True, text=True)
    return result.returncode == 0


def log_failure(message):
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FAILURE_LOG, "a") as f:
        f.write(f"{datetime.now().isoformat()} {message}\n")


def main():
    history = load_json(DATA_DIR / "sim_history.json", [])
    balance_data = load_json(DATA_DIR / "sim_balance.json", {"balance": 0.0, "initial": 0.0})
    positions = load_json(DATA_DIR / "sim_positions.json", {})

    now_dt = datetime.now()
    summary = compute_summary(history, balance_data, positions, now_dt.timestamp())
    message = format_report_message(summary, is_bot_alive(), now_dt)

    try:
        response = httpx.post(
            BAILEYS_URL,
            json={"to": TO_NUMBER, "text": message, "sessionId": SESSION_ID},
            timeout=15.0,
        )
        response.raise_for_status()
    except Exception as e:
        log_failure(f"FALLO envio: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

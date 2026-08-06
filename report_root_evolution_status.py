#!/usr/bin/env python3
"""
Chequeo único (one-shot, vía launchd) de cómo va el decisor evolutivo de
ROOT unos días después de dejarlo corriendo en vivo (mergeado a main y
reiniciado el 05/08/26). Manda un resumen honesto por WhatsApp — mismo
canal que report_whatsapp.py (baileys-server de CRM-Auto) — y se
auto-desinstala del launchd después de correr: no está pensado para correr
en loop, y StartCalendarInterval no soporta año, así que sin auto-limpieza
volvería a dispararse el mismo día el año que viene.
"""
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

REPO_DIR = Path(__file__).resolve().parent
DATA_DIR = REPO_DIR / "data"
LOG_PATH = REPO_DIR / "logs" / "launchd_bot.log"
FAILURE_LOG = REPO_DIR / "logs" / "report_failures.log"

BAILEYS_URL = "http://localhost:3002/send"
SESSION_ID = "HzyDWOrkySUVdcoYUszi"
TO_NUMBER = "18295080887"

PLIST_LABEL = "com.branel.botsolana.rootcheck"
PLIST_PATH = REPO_DIR / "launchd" / "com.branel.botsolana.rootcheck.plist"

PROMOTION_MIN_TRADES = 20  # mismo default que ROOT_PROMOTION_MIN_TRADES


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text().splitlines() if line.strip())


def _bot_pid() -> str | None:
    """Lee el PID vía launchctl list — refleja lo que launchd tiene cargado
    de verdad, no un pgrep que puede matchear procesos zombie."""
    result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if "com.branel.botsolana.bot" in line and "rootcheck" not in line:
            pid = line.split()[0]
            return pid if pid != "-" else None
    return None


def _max_generation() -> int | None:
    """El número de generación no se persiste aparte — se infiere de los
    config_id de la población actual (root_evolution.mutate() los nombra
    'genN-i'). Si no hay ningún config_id con ese patrón, no se sabe."""
    population = _load_json(DATA_DIR / "root_population.json", [])
    max_gen = None
    for cfg in population:
        m = re.match(r"^gen(\d+)-", cfg.get("config_id", ""))
        if m:
            gen = int(m.group(1))
            max_gen = gen if max_gen is None else max(max_gen, gen)
    return max_gen


def _champion_promotions() -> list[str]:
    if not LOG_PATH.exists():
        return []
    return [line.strip() for line in LOG_PATH.read_text().splitlines() if "promovido a campeón" in line]


def _recent_root_warnings() -> list[str]:
    """Solo errores/warnings de los módulos nuevos de ROOT — no todo el
    ruido del log del bot completo (que tiene sus propios errores viejos
    sin relación)."""
    if not LOG_PATH.exists():
        return []
    tags = ("[root_evolution]", "[root_decider]", "[root_sim]", "[root_validation_pool]")
    return [
        line.strip() for line in LOG_PATH.read_text().splitlines()
        if any(t in line for t in tags) and ("WARNING" in line or "ERROR" in line)
    ]


def build_report() -> str:
    champion = _load_json(DATA_DIR / "root_champion.json", None)
    champion_trades = _count_jsonl(DATA_DIR / "root_sim_history.json")
    max_gen = _max_generation()
    promotions = _champion_promotions()
    warnings = _recent_root_warnings()
    pid = _bot_pid()

    lines = ["🧬 *ROOT — chequeo a los días de dejarlo corriendo*", ""]

    lines.append(f"✅ Bot corriendo (PID {pid})" if pid else "🔴 Bot NO está corriendo en launchd — revisar")

    if champion:
        lines.append(f"👑 Campeón actual: `{champion.get('config_id')}`")
    else:
        lines.append("⚠️ No hay root_champion.json todavía — el evolutivo nunca completó un ciclo")

    lines.append(
        f"🧪 Generación actual (según población): {max_gen}"
        if max_gen is not None
        else "🧪 Sin datos de generación (root_population.json vacío o el evolutivo no arrancó)"
    )
    lines.append(f"📊 Trades del campeón acumulados: {champion_trades}")

    lines.append("")
    lines.append("*Candidatos en validación:*")
    candidate_files = sorted(glob.glob(str(DATA_DIR / "root_validation_*_history.json")))
    if not candidate_files:
        lines.append("— ninguno con trades todavía")
    for f in candidate_files:
        name = Path(f).stem.replace("root_validation_", "").replace("_history", "")
        n = _count_jsonl(Path(f))
        flag = "✅ lista para evaluar promoción" if n >= PROMOTION_MIN_TRADES else f"({n}/{PROMOTION_MIN_TRADES})"
        lines.append(f"— {name}: {n} trades {flag}")

    lines.append("")
    if promotions:
        lines.append(f"🔁 Hubo {len(promotions)} promoción(es) de campeón en el log:")
        lines.extend(f"  {p}" for p in promotions[-3:])
    else:
        lines.append("🔁 Sin promociones de campeón todavía")

    if warnings:
        lines.append("")
        lines.append(
            f"⚠️ {len(warnings)} warning(s)/error(es) de los módulos de ROOT en el log — "
            f"revisar logs/launchd_bot.log"
        )

    return "\n".join(lines)


def _self_unload():
    """Chequeo de una sola vez: se saca solo de launchd y borra su propio
    plist después de correr, para no quedar reprogramado sin querer."""
    try:
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{PLIST_LABEL}"],
            capture_output=True,
        )
        if PLIST_PATH.exists():
            PLIST_PATH.unlink()
    except Exception:
        pass  # no crítico — peor caso, vuelve a correr el mismo día el año que viene


def main():
    message = build_report()
    try:
        response = httpx.post(
            BAILEYS_URL,
            json={"to": TO_NUMBER, "text": message, "sessionId": SESSION_ID},
            timeout=15.0,
        )
        response.raise_for_status()
    except Exception as e:
        FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(FAILURE_LOG, "a") as f:
            f.write(f"{datetime.now().isoformat()} FALLO envio chequeo ROOT: {e}\n{message}\n---\n")
        _self_unload()
        sys.exit(1)

    _self_unload()


if __name__ == "__main__":
    main()

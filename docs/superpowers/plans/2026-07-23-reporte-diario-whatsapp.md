# Reporte diario de BotSolana por WhatsApp — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BotSolana y el baileys-server de CRM-auto corren local de forma estable (auto-restart), y todos los días a las 21:00 llega un WhatsApp con el resumen de las últimas 24h del bot.

**Architecture:** Tres LaunchAgents de macOS independientes (bot, baileys-server, reporte programado) más un módulo Python nuevo (`copytrade/report.py`) con la lógica pura de cálculo/formato, cubierto por tests, y un script CLI (`report_whatsapp.py`) que hace la I/O real (leer JSON, chequear proceso, POST a WhatsApp).

**Tech Stack:** Python 3.10 (pyenv shim en `/Users/branel/.pyenv/shims/python3`), `httpx` (ya en requirements.txt), pytest, Node 22 (`/usr/local/bin/node`) para baileys-server, `launchd` (macOS) para auto-restart y scheduling.

## Global Constraints

- El bot permanece en modo SIM / copywallet-only — NO tocar `LIVE_MODE`, `AUTONOMOUS_MODE` ni `LEARNER_SCANNER_ENABLED` en `.env`.
- No se reactiva ni se diagnostica Railway — el bot corre 100% local de ahora en adelante.
- Solo resumen diario, sin alertas en tiempo real por trade.
- Se reusa la sesión WhatsApp ya vinculada de `baileys-server` — `sessionId = "HzyDWOrkySUVdcoYUszi"` — no se crea sesión nueva.
- Número destino fijo: `"18295080887"`.
- Hora del reporte: 21:00 todos los días.
- Mecanismo de keep-alive y scheduling: `launchd` (LaunchAgents de usuario, `gui/501`), no `cron`.

---

## File Structure

- **Create:** `copytrade/report.py` — funciones puras: `compute_summary()`, `format_report_message()`.
- **Create:** `tests/test_report.py` — tests unitarios de esas dos funciones.
- **Create:** `report_whatsapp.py` (raíz del repo) — script CLI: lee los JSON de `data/`, chequea si el proceso del bot está vivo, arma el mensaje y lo manda por WhatsApp.
- **Create:** `launchd/com.branel.botsolana.bot.plist` — mantiene `main.py` corriendo.
- **Create:** `launchd/com.branel.botsolana.report.plist` — dispara `report_whatsapp.py` a las 21:00.
- **Create (en el repo CRM-auto):** `/Users/branel/CRM-auto/baileys-server/launchd/com.branel.baileys.server.plist` — mantiene `baileys-server` corriendo.

---

### Task 1: Lógica del resumen (`copytrade/report.py`) con TDD

**Files:**
- Create: `copytrade/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Produces: `compute_summary(history: list[dict], balance_data: dict, positions: dict, now_ts: float, window_hours: float = 24.0) -> dict` con claves `trades_24h, wins_24h, losses_24h, win_rate_24h, pnl_24h_usd, best_trade, worst_trade, open_positions, balance, initial, roi_pct`.
- Produces: `format_report_message(summary: dict, bot_alive: bool, now_dt: datetime) -> str`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_report.py`:

```python
import time
from datetime import datetime

from copytrade.report import compute_summary, format_report_message


def _trade(hours_ago, won, pnl_usd, pnl_pct, symbol="ABC", wallet="Theo"):
    now = time.time()
    return {
        "timestamp": now - hours_ago * 3600,
        "won": won,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "symbol": symbol,
        "wallet_label": wallet,
    }


def test_compute_summary_no_trades():
    now = time.time()
    summary = compute_summary([], {"balance": 100.0, "initial": 20.0}, {}, now)
    assert summary["trades_24h"] == 0
    assert summary["win_rate_24h"] == 0.0
    assert summary["pnl_24h_usd"] == 0.0
    assert summary["best_trade"] is None
    assert summary["worst_trade"] is None
    assert summary["open_positions"] == 0
    assert summary["roi_pct"] == 400.0


def test_compute_summary_mixed_trades_within_window():
    now = time.time()
    history = [
        _trade(1, True, 50.0, 20.0, symbol="WIN1"),
        _trade(2, False, -10.0, -5.0, symbol="LOSS1"),
        _trade(25, True, 999.0, 999.0, symbol="TOO_OLD"),
    ]
    summary = compute_summary(history, {"balance": 140.0, "initial": 20.0}, {"a": {}}, now)
    assert summary["trades_24h"] == 2
    assert summary["wins_24h"] == 1
    assert summary["losses_24h"] == 1
    assert summary["win_rate_24h"] == 50.0
    assert summary["pnl_24h_usd"] == 40.0
    assert summary["best_trade"]["symbol"] == "WIN1"
    assert summary["worst_trade"]["symbol"] == "LOSS1"
    assert summary["open_positions"] == 1


def test_compute_summary_all_losses_best_is_least_bad():
    now = time.time()
    history = [
        _trade(1, False, -10.0, -5.0, symbol="L1"),
        _trade(2, False, -20.0, -8.0, symbol="L2"),
    ]
    summary = compute_summary(history, {"balance": 70.0, "initial": 100.0}, {}, now)
    assert summary["win_rate_24h"] == 0.0
    assert summary["best_trade"]["symbol"] == "L1"
    assert summary["worst_trade"]["symbol"] == "L2"


def test_format_report_message_with_activity():
    summary = {
        "trades_24h": 2, "wins_24h": 1, "losses_24h": 1, "win_rate_24h": 50.0,
        "pnl_24h_usd": 40.0,
        "best_trade": {"symbol": "WIN1", "wallet_label": "Theo", "pnl_pct": 20.0},
        "worst_trade": {"symbol": "LOSS1", "wallet_label": "Cented", "pnl_pct": -5.0},
        "open_positions": 3, "balance": 140.0, "initial": 20.0, "roi_pct": 600.0,
    }
    msg = format_report_message(summary, bot_alive=True, now_dt=datetime(2026, 7, 23, 21, 0))
    assert "🟢 corriendo" in msg
    assert "Balance: $140.00 (inicial $20.00 → ROI +600.0%)" in msg
    assert "2 trades" in msg
    assert "WR 50%" in msg
    assert "Mejor: WIN1 Theo +20.0%" in msg
    assert "Peor: LOSS1 Cented -5.0%" in msg
    assert "Posiciones abiertas: 3" in msg


def test_format_report_message_no_activity_bot_down():
    summary = {
        "trades_24h": 0, "wins_24h": 0, "losses_24h": 0, "win_rate_24h": 0.0,
        "pnl_24h_usd": 0.0, "best_trade": None, "worst_trade": None,
        "open_positions": 0, "balance": 20.0, "initial": 20.0, "roi_pct": 0.0,
    }
    msg = format_report_message(summary, bot_alive=False, now_dt=datetime(2026, 7, 23, 21, 0))
    assert "🔴 proceso caído" in msg
    assert "sin actividad" in msg
    assert "Mejor" not in msg
    assert "Peor" not in msg
```

- [ ] **Step 2: Correr los tests y confirmar que fallan**

Run: `cd /Users/branel/Desktop/botsolana && python3 -m pytest tests/test_report.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'copytrade.report'` (o `ImportError`).

- [ ] **Step 3: Implementar `copytrade/report.py`**

```python
"""Cálculo y formato del resumen diario de BotSolana (sin I/O)."""
from datetime import datetime


def compute_summary(history, balance_data, positions, now_ts, window_hours=24.0):
    window_start = now_ts - window_hours * 3600
    recent = [t for t in history if t.get("timestamp", 0) >= window_start]
    wins = [t for t in recent if t.get("won")]
    losses = [t for t in recent if not t.get("won")]
    pnl_24h = sum(t.get("pnl_usd", 0.0) for t in recent)
    best = max(recent, key=lambda t: t.get("pnl_pct", float("-inf")), default=None)
    worst = min(recent, key=lambda t: t.get("pnl_pct", float("inf")), default=None)

    balance = balance_data.get("balance", 0.0)
    initial = balance_data.get("initial", 0.0)
    roi_pct = ((balance / initial) - 1) * 100 if initial else 0.0

    return {
        "trades_24h": len(recent),
        "wins_24h": len(wins),
        "losses_24h": len(losses),
        "win_rate_24h": (len(wins) / len(recent) * 100) if recent else 0.0,
        "pnl_24h_usd": pnl_24h,
        "best_trade": best,
        "worst_trade": worst,
        "open_positions": len(positions),
        "balance": balance,
        "initial": initial,
        "roi_pct": roi_pct,
    }


def format_report_message(summary, bot_alive, now_dt):
    estado = "🟢 corriendo" if bot_alive else "🔴 proceso caído"
    fecha = now_dt.strftime("%d/%m")
    hora = now_dt.strftime("%H:%M")

    lines = [
        f"🤖 BotSolana — Reporte {fecha} {hora}",
        f"Estado: {estado}",
        f"Balance: ${summary['balance']:,.2f} (inicial ${summary['initial']:,.2f} "
        f"→ ROI {summary['roi_pct']:+.1f}%)",
    ]

    if summary["trades_24h"] > 0:
        lines.append(
            f"Últimas 24h: {summary['trades_24h']} trades | "
            f"WR {summary['win_rate_24h']:.0f}% "
            f"({summary['wins_24h']}W/{summary['losses_24h']}L) | "
            f"P&L ${summary['pnl_24h_usd']:+,.2f}"
        )
        best = summary["best_trade"]
        worst = summary["worst_trade"]
        if best:
            lines.append(
                f"Mejor: {best.get('symbol', '?')} {best.get('wallet_label', '?')} "
                f"{best.get('pnl_pct', 0):+.1f}%"
            )
        if worst:
            lines.append(
                f"Peor: {worst.get('symbol', '?')} {worst.get('wallet_label', '?')} "
                f"{worst.get('pnl_pct', 0):+.1f}%"
            )
    else:
        lines.append("Últimas 24h: sin actividad")

    lines.append(f"Posiciones abiertas: {summary['open_positions']}")
    return "\n".join(lines)
```

- [ ] **Step 4: Correr los tests y confirmar que pasan**

Run: `cd /Users/branel/Desktop/botsolana && python3 -m pytest tests/test_report.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/branel/Desktop/botsolana
git add copytrade/report.py tests/test_report.py
git commit -m "feat: lógica pura del resumen diario para el reporte por WhatsApp"
```

---

### Task 2: Script CLI `report_whatsapp.py`

**Files:**
- Create: `report_whatsapp.py`

**Interfaces:**
- Consumes: `compute_summary`, `format_report_message` de `copytrade/report.py` (Task 1).
- Produces: script ejecutable standalone, sin funciones consumidas por otras tasks.

- [ ] **Step 1: Escribir `report_whatsapp.py`**

```python
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
```

- [ ] **Step 2: Verificación manual end-to-end (no es un unit test — depende de red/WhatsApp real)**

El `baileys-server` ya está corriendo local (arrancado manualmente durante el diseño, sesión `HzyDWOrkySUVdcoYUszi` conectada y verificada con un mensaje de prueba).

Run: `cd /Users/branel/Desktop/botsolana && python3 report_whatsapp.py; echo "exit: $?"`
Expected: `exit: 0`, y llega un WhatsApp real al `18295080887` con el resumen (con los datos actuales de `data/`, dirá `🔴 proceso caído` porque el bot todavía no se relanzó — eso se corrige en el Task 3).

- [ ] **Step 3: Commit**

```bash
cd /Users/branel/Desktop/botsolana
git add report_whatsapp.py
git commit -m "feat: script CLI que arma y envía el reporte diario por WhatsApp"
```

---

### Task 3: LaunchAgent que mantiene `main.py` corriendo

**Files:**
- Create: `launchd/com.branel.botsolana.bot.plist`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: proceso `main.py` vivo y auto-relanzado, del que Task 2 (`is_bot_alive()`) depende para reportar el estado correcto.

- [ ] **Step 1: Crear el plist**

```bash
mkdir -p /Users/branel/Desktop/botsolana/launchd
```

Crear `/Users/branel/Desktop/botsolana/launchd/com.branel.botsolana.bot.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.branel.botsolana.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/branel/.pyenv/shims/python3</string>
        <string>/Users/branel/Desktop/botsolana/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/branel/Desktop/botsolana</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/branel/Desktop/botsolana/logs/launchd_bot.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/branel/Desktop/botsolana/logs/launchd_bot.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Instalar y arrancar el LaunchAgent**

```bash
cp /Users/branel/Desktop/botsolana/launchd/com.branel.botsolana.bot.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.branel.botsolana.bot.plist
```

- [ ] **Step 3: Verificar que quedó corriendo**

Run: `sleep 5 && launchctl print gui/501/com.branel.botsolana.bot | grep -E "state|pid"`
Expected: `state = running` y un `pid = <número>`.

Run: `pgrep -f /Users/branel/Desktop/botsolana/main.py`
Expected: imprime un PID (el mismo proceso).

- [ ] **Step 4: Commit del plist (no del cambio en ~/Library, que no es parte del repo)**

```bash
cd /Users/branel/Desktop/botsolana
git add launchd/com.branel.botsolana.bot.plist
git commit -m "feat: LaunchAgent que mantiene main.py corriendo con auto-restart"
```

---

### Task 4: LaunchAgent que mantiene `baileys-server` corriendo

**Files:**
- Create: `/Users/branel/CRM-auto/baileys-server/launchd/com.branel.baileys.server.plist`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: `baileys-server` vivo en `:3002` de forma estable, del que Task 2 depende para poder enviar el WhatsApp.

- [ ] **Step 1: Matar la instancia manual arrancada durante el diseño (para evitar choque de puerto)**

```bash
lsof -ti :3002 | xargs -r kill
sleep 1
lsof -i :3002
```

Expected: el segundo `lsof -i :3002` no imprime nada (puerto libre).

- [ ] **Step 2: Crear el plist**

```bash
mkdir -p /Users/branel/CRM-auto/baileys-server/launchd
mkdir -p /Users/branel/CRM-auto/baileys-server/logs
```

Crear `/Users/branel/CRM-auto/baileys-server/launchd/com.branel.baileys.server.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.branel.baileys.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/node</string>
        <string>index.js</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/branel/CRM-auto/baileys-server</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/branel/CRM-auto/baileys-server/logs/launchd_baileys.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/branel/CRM-auto/baileys-server/logs/launchd_baileys.log</string>
</dict>
</plist>
```

- [ ] **Step 3: Instalar y arrancar el LaunchAgent**

```bash
cp /Users/branel/CRM-auto/baileys-server/launchd/com.branel.baileys.server.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.branel.baileys.server.plist
```

- [ ] **Step 4: Verificar que quedó corriendo y la sesión de WhatsApp sigue conectada**

Run: `sleep 5 && launchctl print gui/501/com.branel.baileys.server | grep -E "state|pid"`
Expected: `state = running` y un `pid`.

Run: `curl -s http://localhost:3002/status/HzyDWOrkySUVdcoYUszi`
Expected: `{"sessionId":"HzyDWOrkySUVdcoYUszi","status":"open","connected":true}` (la sesión persiste porque las credenciales quedaron guardadas en Firestore, no dependen del proceso node).

- [ ] **Step 5: Commit**

```bash
cd /Users/branel/CRM-auto
git add baileys-server/launchd/com.branel.baileys.server.plist
git commit -m "feat: LaunchAgent que mantiene baileys-server corriendo con auto-restart"
```

---

### Task 5: LaunchAgent programado del reporte diario (21:00) + verificación end-to-end

**Files:**
- Create: `launchd/com.branel.botsolana.report.plist`

**Interfaces:**
- Consumes: `report_whatsapp.py` (Task 2), depende de que Task 3 y Task 4 ya estén corriendo para que el reporte sea preciso y se pueda enviar.

- [ ] **Step 1: Crear el plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.branel.botsolana.report</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/branel/.pyenv/shims/python3</string>
        <string>/Users/branel/Desktop/botsolana/report_whatsapp.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/branel/Desktop/botsolana</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>21</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/branel/Desktop/botsolana/logs/launchd_report.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/branel/Desktop/botsolana/logs/launchd_report.log</string>
</dict>
</plist>
```

Guardar en `/Users/branel/Desktop/botsolana/launchd/com.branel.botsolana.report.plist`. Nota: sin `KeepAlive` (es una tarea puntual programada, no un proceso persistente).

- [ ] **Step 2: Instalar el LaunchAgent**

```bash
cp /Users/branel/Desktop/botsolana/launchd/com.branel.botsolana.report.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.branel.botsolana.report.plist
```

- [ ] **Step 3: Verificar que quedó agendado**

Run: `launchctl print gui/501/com.branel.botsolana.report | grep -A3 "calendar interval"`
Expected: muestra `hour = 21` y `minute = 0`.

- [ ] **Step 4: Forzar una corrida ahora mismo para verificar el flujo completo (no hace falta esperar a las 21:00)**

```bash
launchctl kickstart gui/501/com.branel.botsolana.report
sleep 3
cat /Users/branel/Desktop/botsolana/logs/launchd_report.log | tail -5
```

Expected: log vacío o sin tracebacks, y llega un WhatsApp real al `18295080887`. Si Task 3 ya dejó `main.py` corriendo, el mensaje debe decir `🟢 corriendo` en vez de `🔴 proceso caído`.

- [ ] **Step 5: Commit**

```bash
cd /Users/branel/Desktop/botsolana
git add launchd/com.branel.botsolana.report.plist
git commit -m "feat: LaunchAgent que dispara el reporte diario por WhatsApp a las 21:00"
```

---

## Self-Review

**Cobertura del spec:**
- Bot corriendo local con auto-restart → Task 3. ✅
- baileys-server corriendo local con auto-restart → Task 4. ✅
- Reporte diario 21:00 al número correcto → Task 5. ✅
- Lógica de cálculo (24h, win rate, mejor/peor, ROI, posiciones abiertas) → Task 1. ✅
- Formato del mensaje según el template del spec → Task 1 (`format_report_message`) + verificado en Task 2/5. ✅
- Chequeo de salud del bot (🟢/🔴) → `is_bot_alive()` en Task 2. ✅
- Manejo de fallo de envío (log local, sin reintento) → `log_failure()` en Task 2. ✅
- Setup manual del QR → ya completado durante el diseño (no es una task de código).

**Placeholders:** ninguno — cada step tiene código completo o comandos exactos con output esperado.

**Consistencia de tipos:** `compute_summary()` (Task 1) devuelve las claves que `format_report_message()` (Task 1) y `report_whatsapp.py` (Task 2) consumen — mismos nombres (`trades_24h`, `best_trade`, `roi_pct`, etc.) verificados en ambos.

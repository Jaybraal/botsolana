# Fix Modo Autónomo BotSolana — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detener la sangría del modo autónomo (19.6% WR, -$3,920 desde 01/07) corrigiendo las 4 causas raíz verificadas: instancias múltiples, precio de entrada obsoleto, criterios que dejan pasar tokens perdedores, y monitores SL/TP que mueren sin reemplazo.

**Architecture:** Todos los cambios viven en `copytrade/learner_scanner.py` (modo autónomo), un módulo nuevo `utils/singleton.py` (lock de instancia única) y 3 líneas en `main.py`. El flujo copy-trading (rentable: 55.6% WR, +$2,600) NO se toca.

**Tech Stack:** Python 3.10, asyncio, httpx, pytest (suite actual: 29 tests verdes), fcntl (stdlib).

## Global Constraints

- Python 3.10; SOLO stdlib para dependencias nuevas (`fcntl`, ya disponible en macOS/Linux).
- Los 29 tests existentes deben seguir verdes tras CADA tarea: `python3 -m pytest tests/ -q`.
- PROHIBIDO tocar: `copytrade/simulator.py`, `copytrade/executor.py`, `copytrade/watcher.py`, variables de Railway.
- Directorio de trabajo: `/Users/branel/Desktop/botsolana`.
- Mensajes de commit en español, prefijo `fix:`/`feat:`/`chore:` (estilo del repo).
- Commit SOLO los archivos de cada tarea (el repo puede tener otros archivos sueltos — no hacer `git add -A`).

## Evidencia de diagnóstico (contexto para el implementador)

1. **5 instancias de `main.py` corriendo a la vez** (PIDs 66441, 72498, 77031, 78283, 79269). Cada una tiene su `_auto_positions` en memoria pero comparten `data/sim_positions.json` → monitores huérfanos, y el auto-close del simulador (30 min) vende a precio derrumbado. Evidencia: holds de 55–72 min con `MAX_HOLD_MIN=5`, pérdidas de -66% con SL de -6%.
2. **Precio de entrada obsoleto:** el scan usa datos de DexScreener con hasta 5 min de retraso; hay pérdidas de -66% con hold de 0.5 min (una sola tick) = se "compró" a un precio que ya no existía.
3. **Criterios 3/7 dejan pasar perdedores:** WR por bucket del histórico real (140 trades con contexto): liquidez <$10k → 8% WR (38 trades); change_1h <50% → 12% WR (93 trades). Mejores buckets: liq $30–60k → 41%, change_1h ≥50% → 33-35%.
4. **`_monitor_position` se lanza con `asyncio.create_task` sin supervisión** — si muere, nadie lo relanza y la posición queda sin SL/TP.

---

### Task 0: Checkpoint del estado actual

**Files:**
- Modify: ninguno (solo git)

**Interfaces:**
- Produces: working tree limpio para que los diffs de las tareas siguientes sean legibles.

- [ ] **Step 1: Commitear la optimización pendiente del 01/07 (ya está corriendo en producción local)**

```bash
cd /Users/branel/Desktop/botsolana
git add copytrade/learner_scanner.py start_bot.sh
git commit -m "chore: checkpoint optimización 01/07 (SCORE_THRESH 38, criterios 3/7) + start_bot.sh"
```

- [ ] **Step 2: Verificar suite verde de base**

Run: `python3 -m pytest tests/ -q`
Expected: `29 passed`

---

### Task 1: Lock de instancia única

**Files:**
- Create: `utils/singleton.py`
- Modify: `main.py` (bloque `if __name__ == "__main__":`, ~línea 245)
- Test: `tests/test_singleton.py`

**Interfaces:**
- Produces: `acquire_lock(path: str) -> bool` en `utils/singleton.py`. Devuelve `True` si somos la única instancia; `False` si otro proceso tiene el lock. El lock se mantiene mientras viva el proceso (no cerrar el handle).

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_singleton.py`:

```python
import fcntl

import pytest

from utils.singleton import acquire_lock


def test_acquire_lock_devuelve_true_primera_vez(tmp_path):
    lock = tmp_path / "bot.lock"
    assert acquire_lock(str(lock)) is True


def test_lock_bloquea_segunda_instancia(tmp_path):
    lock = tmp_path / "bot2.lock"
    assert acquire_lock(str(lock)) is True
    # Simula otra instancia: un file descriptor nuevo sobre el mismo path
    f = open(lock, "w")
    with pytest.raises(OSError):
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    f.close()
```

- [ ] **Step 2: Verificar que falla**

Run: `python3 -m pytest tests/test_singleton.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'utils.singleton'`

- [ ] **Step 3: Implementar `utils/singleton.py`**

```python
"""Lock de instancia única — evita que corran varias copias de main.py a la vez."""
import fcntl
import os

_lock_handle = None  # mantener referencia viva: si se cierra, el lock se libera


def acquire_lock(path: str) -> bool:
    """Toma un flock exclusivo no bloqueante sobre `path`.

    True → somos la única instancia (el lock queda tomado de por vida del proceso).
    False → otra instancia ya tiene el lock.
    """
    global _lock_handle
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    handle.write(str(os.getpid()))
    handle.flush()
    _lock_handle = handle
    return True
```

- [ ] **Step 4: Verificar que pasa**

Run: `python3 -m pytest tests/test_singleton.py -v`
Expected: 2 PASS

- [ ] **Step 5: Integrar en `main.py`**

En `main.py`, dentro del bloque `if __name__ == "__main__":` (~línea 245), como PRIMERAS líneas del bloque (antes de cualquier llamada a `main()`), insertar:

```python
    from utils.singleton import acquire_lock
    if not acquire_lock("data/bot.lock"):
        print("❌ Ya hay otra instancia de BotSolana corriendo (data/bot.lock). Saliendo.")
        sys.exit(1)
```

(`sys` ya está importado en la línea 16.)

- [ ] **Step 6: Suite completa + commit**

Run: `python3 -m pytest tests/ -q`
Expected: `31 passed`

```bash
git add utils/singleton.py tests/test_singleton.py main.py
git commit -m "fix: lock de instancia única — evita múltiples main.py simultáneos"
```

---

### Task 2: Precio fresco al abrir posición autónoma

**Files:**
- Modify: `copytrade/learner_scanner.py` — función `_open_position` (~línea 317) y bloque Config (~línea 46)
- Test: `tests/test_open_position_precio.py`

**Interfaces:**
- Consumes: `_fetch_current_price(mint, pair_address) -> float` (ya existe, línea 179).
- Produces: `_open_position` re-verifica el precio en vivo antes de comprar; usa el precio fresco como entrada; aborta si no hay precio o si se movió más de `MAX_ENTRY_DRIFT_PCT` (env `AUTO_MAX_ENTRY_DRIFT_PCT`, default 10) respecto al precio del scan.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_open_position_precio.py`:

```python
import asyncio

import pytest

import copytrade.learner_scanner as ls


@pytest.fixture(autouse=True)
def _aislar_estado(monkeypatch):
    ls._auto_positions.clear()
    monkeypatch.setattr(ls, "_get_sol_price", lambda: 150.0)

    async def _fake_monitor(mint, symbol):
        return None

    monkeypatch.setattr(ls, "_monitor_position", _fake_monitor)
    yield
    ls._auto_positions.clear()


def _token(price=1.0):
    return {
        "price_usd": price,
        "symbol": "TST",
        "program": "PumpSwap",
        "pair_address": "PAIRX",
    }


def test_usa_precio_fresco_como_entrada(monkeypatch):
    compras = []

    async def _fake_exec(swap):
        compras.append(swap)
        return True

    monkeypatch.setattr(ls, "execute_copy", _fake_exec)
    monkeypatch.setattr(ls, "_fetch_current_price", lambda m, p="": 1.05)

    asyncio.run(ls._open_position("MINT1", _token(1.0), "test"))

    assert "MINT1" in ls._auto_positions
    assert ls._auto_positions["MINT1"]["entry_price_usd"] == pytest.approx(1.05)
    assert len(compras) == 1


def test_aborta_si_precio_se_movio_mas_del_limite(monkeypatch):
    async def _fake_exec(swap):
        raise AssertionError("no debe comprar con precio movido >10%")

    monkeypatch.setattr(ls, "execute_copy", _fake_exec)
    monkeypatch.setattr(ls, "_fetch_current_price", lambda m, p="": 1.5)  # +50%

    asyncio.run(ls._open_position("MINT2", _token(1.0), "test"))

    assert "MINT2" not in ls._auto_positions


def test_aborta_si_no_hay_precio_fresco(monkeypatch):
    async def _fake_exec(swap):
        raise AssertionError("no debe comprar sin precio")

    monkeypatch.setattr(ls, "execute_copy", _fake_exec)
    monkeypatch.setattr(ls, "_fetch_current_price", lambda m, p="": 0.0)

    asyncio.run(ls._open_position("MINT3", _token(1.0), "test"))

    assert "MINT3" not in ls._auto_positions
```

- [ ] **Step 2: Verificar que fallan**

Run: `python3 -m pytest tests/test_open_position_precio.py -v`
Expected: `test_usa_precio_fresco_como_entrada` FAIL (entrada == 1.0, no 1.05); los otros dos FAIL con AssertionError "no debe comprar" (hoy compra directo con el precio del scan).

- [ ] **Step 3: Implementar**

En el bloque Config de `copytrade/learner_scanner.py` (junto a `MONITOR_TICK`, ~línea 56), añadir:

```python
MAX_ENTRY_DRIFT_PCT = float(os.getenv("AUTO_MAX_ENTRY_DRIFT_PCT", "10"))  # % máximo de deriva scan→ahora
```

Reemplazar el inicio de `_open_position` (desde `entry_price = token_info.get("price_usd", 0)` hasta justo antes de `_auto_positions[mint] = {`) por:

```python
    scan_price   = token_info.get("price_usd", 0)
    symbol       = token_info.get("symbol", mint[:6])
    program      = token_info.get("program", "PumpSwap")
    pair_address = token_info.get("pair_address", "")
    sol_price    = _get_sol_price()

    # El precio del scan puede tener hasta 5 min de retraso (SCAN_INTERVAL).
    # Re-verificar en vivo: sin precio fresco no se compra; deriva grande = pump ya pasó.
    fresh = await asyncio.get_running_loop().run_in_executor(
        None, _fetch_current_price, mint, pair_address
    )
    if fresh <= 0:
        log.info(f"[learner] ⛔ {symbol}: sin precio fresco verificable — skip")
        return
    if scan_price > 0:
        drift_pct = abs(fresh - scan_price) / scan_price * 100
        if drift_pct > MAX_ENTRY_DRIFT_PCT:
            log.info(
                f"[learner] ⛔ {symbol}: precio se movió {drift_pct:.1f}% desde el scan "
                f"(límite {MAX_ENTRY_DRIFT_PCT:.0f}%) — skip"
            )
            return
    entry_price = fresh
```

El dict `_auto_positions[mint] = {...}` y el `buy_swap` quedan igual (ya usan `entry_price`, `pair_address`, `symbol`, `program`, `sol_price`).

- [ ] **Step 4: Verificar que pasan**

Run: `python3 -m pytest tests/test_open_position_precio.py -v`
Expected: 3 PASS

- [ ] **Step 5: Suite completa + commit**

Run: `python3 -m pytest tests/ -q`
Expected: `34 passed`

```bash
git add copytrade/learner_scanner.py tests/test_open_position_precio.py
git commit -m "fix: re-verificar precio en vivo antes de abrir posición autónoma (deriva max 10%)"
```

---

### Task 3: Filtros duros basados en datos (liquidez y momentum)

**Files:**
- Modify: `copytrade/learner_scanner.py` — bloque Config y función `_score_and_decide` (~línea 137)
- Test: `tests/test_filtros_duros.py`

**Interfaces:**
- Produces: `_score_and_decide` rechaza SIEMPRE (sin importar el resto de criterios) tokens con `liquidity_usd < 10000` o `price_change_1h < 50`. Umbrales configurables por env: `AUTO_HARD_MIN_LIQUIDITY_USD` (default 10000), `AUTO_HARD_MIN_CHANGE_1H_PCT` (default 50).

**Racional (datos reales, 140 trades):** liq <$10k → 8% WR; change_1h <50% → 12% WR. Estos buckets generaron la mayoría de las pérdidas. Con `CRITERIA_MATCH=3/7` estos tokens pasaban el filtro blando.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_filtros_duros.py`:

```python
import copytrade.learner_scanner as ls


def _token(liq=20000.0, ch1=80.0):
    return {
        "price_usd": 1.0,
        "symbol": "TST",
        "liquidity_usd": liq,
        "price_change_1h": ch1,
    }


def test_rechaza_liquidez_baja():
    ok, reason = ls._score_and_decide(_token(liq=5000.0))
    assert ok is False
    assert "liquidez" in reason.lower()


def test_rechaza_momentum_bajo():
    ok, reason = ls._score_and_decide(_token(ch1=10.0))
    assert ok is False
    assert "change_1h" in reason.lower()


def test_filtro_duro_no_depende_de_otros_criterios():
    # liquidez y momentum buenos en un token que fallaría el resto:
    # el rechazo (si ocurre) NO debe ser por los filtros duros
    ok, reason = ls._score_and_decide(_token(liq=45000.0, ch1=120.0))
    assert "filtro duro" not in reason.lower()
```

- [ ] **Step 2: Verificar que fallan**

Run: `python3 -m pytest tests/test_filtros_duros.py -v`
Expected: los dos primeros FAIL (hoy esos tokens pueden pasar o ser rechazados por otra razón — el assert de la razón falla). El tercero puede pasar; si pasa, no es problema.

Nota: `_score_and_decide` llama `stat_score` y `load_rules` — son locales (sin red), no necesitan mock.

- [ ] **Step 3: Implementar**

En el bloque Config de `copytrade/learner_scanner.py`, añadir:

```python
# Filtros DUROS — datos reales (140 trades): liq<$10k → 8% WR, change_1h<50% → 12% WR
HARD_MIN_LIQUIDITY_USD = float(os.getenv("AUTO_HARD_MIN_LIQUIDITY_USD", "10000"))
HARD_MIN_CHANGE_1H_PCT = float(os.getenv("AUTO_HARD_MIN_CHANGE_1H_PCT", "50"))
```

En `_score_and_decide`, inmediatamente después del check `if not token_info.get("price_usd"):`, añadir:

```python
    # Filtros duros: no negociables aunque el resto de criterios pase
    liq = token_info.get("liquidity_usd") or 0
    if liq < HARD_MIN_LIQUIDITY_USD:
        return False, (
            f"filtro duro: liquidez ${liq:,.0f} < ${HARD_MIN_LIQUIDITY_USD:,.0f} "
            f"(WR histórico 8%)"
        )
    ch1 = token_info.get("price_change_1h")
    if ch1 is not None and ch1 < HARD_MIN_CHANGE_1H_PCT:
        return False, (
            f"filtro duro: change_1h {ch1:.0f}% < {HARD_MIN_CHANGE_1H_PCT:.0f}% "
            f"(WR histórico 12%)"
        )
```

- [ ] **Step 4: Verificar que pasan**

Run: `python3 -m pytest tests/test_filtros_duros.py -v`
Expected: 3 PASS

- [ ] **Step 5: Suite completa + commit**

Run: `python3 -m pytest tests/ -q`
Expected: `37 passed`

```bash
git add copytrade/learner_scanner.py tests/test_filtros_duros.py
git commit -m "fix: filtros duros liq>=10k y change_1h>=50 (buckets con 8%/12% WR histórico)"
```

---

### Task 4: Monitores supervisados con reconciliación periódica

**Files:**
- Modify: `copytrade/learner_scanner.py` — estado global (~línea 71), `_open_position`, `_monitor_position`, `watch_learner_scanner` (~línea 495)
- Test: `tests/test_ensure_monitor.py`

**Interfaces:**
- Produces:
  - `_monitor_tasks: dict[str, asyncio.Task]` — registro de monitores vivos por mint.
  - `_ensure_monitor(mint: str, symbol: str) -> bool` — lanza un monitor si no hay uno vivo; `True` si lanzó uno nuevo, `False` si ya había.
  - `_reconcile_loop()` — corrutina infinita: cada 60s recupera huérfanas de `sim_positions.json` y relanza monitores muertos.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_ensure_monitor.py`:

```python
import asyncio

import pytest

import copytrade.learner_scanner as ls


@pytest.fixture(autouse=True)
def _aislar_estado(monkeypatch):
    ls._auto_positions.clear()
    ls._monitor_tasks.clear()

    async def _fake_monitor(mint, symbol):
        await asyncio.sleep(3600)  # simula monitor vivo

    monkeypatch.setattr(ls, "_monitor_position", _fake_monitor)
    yield
    for t in ls._monitor_tasks.values():
        t.cancel()
    ls._monitor_tasks.clear()
    ls._auto_positions.clear()


def test_ensure_monitor_lanza_y_no_duplica():
    async def run():
        assert ls._ensure_monitor("M1", "TST") is True
        assert ls._ensure_monitor("M1", "TST") is False  # ya hay uno vivo

    asyncio.run(run())


def test_ensure_monitor_relanza_si_murio():
    async def run():
        assert ls._ensure_monitor("M2", "TST") is True
        ls._monitor_tasks["M2"].cancel()
        await asyncio.sleep(0.05)  # deja que la cancelación se propague y la task quede done()
        assert ls._ensure_monitor("M2", "TST") is True  # relanzado

    asyncio.run(run())
```

- [ ] **Step 2: Verificar que fallan**

Run: `python3 -m pytest tests/test_ensure_monitor.py -v`
Expected: FAIL con `AttributeError: module ... has no attribute '_monitor_tasks'`

- [ ] **Step 3: Implementar**

En `copytrade/learner_scanner.py`, junto al estado global (~línea 71, donde está `_auto_positions`), añadir:

```python
_monitor_tasks: dict[str, asyncio.Task] = {}  # monitores vivos por mint
```

Después de `_monitor_position` (tras la línea 314), añadir:

```python
def _ensure_monitor(mint: str, symbol: str) -> bool:
    """Lanza un monitor para `mint` si no hay uno vivo. True si lanzó uno nuevo."""
    task = _monitor_tasks.get(mint)
    if task is not None and not task.done():
        return False
    _monitor_tasks[mint] = asyncio.create_task(_monitor_position(mint, symbol))
    return True


async def _reconcile_loop():
    """Red de seguridad: cada 60s adopta huérfanas y relanza monitores muertos.

    Sin esto, un monitor que muere deja la posición sin SL/TP y el auto-close
    del simulador (30 min) la vende al precio que sea — así se produjeron
    pérdidas de -66% con SL configurado en -6%.
    """
    while True:
        await asyncio.sleep(60)
        try:
            _recover_orphan_positions()
            for mint, pos in list(_auto_positions.items()):
                if _ensure_monitor(mint, pos.get("symbol", mint[:6])):
                    log.warning(f"[learner] ♻️ monitor relanzado para {pos.get('symbol', mint[:6])}")
            for mint in list(_monitor_tasks):
                if mint not in _auto_positions and _monitor_tasks[mint].done():
                    _monitor_tasks.pop(mint, None)
        except Exception as e:
            log.error(f"[learner] error en _reconcile_loop: {e}")
```

En `_open_position`, reemplazar la última línea `asyncio.create_task(_monitor_position(mint, symbol))` por:

```python
    _ensure_monitor(mint, symbol)
```

En `_monitor_position`, envolver el `while mint in _auto_positions:` completo en try/except para que una excepción quede registrada (y la reconciliación lo relance):

```python
    try:
        while mint in _auto_positions:
            ...  # cuerpo actual sin cambios, re-indentado un nivel
    except Exception as e:
        log.error(f"[learner] 💥 monitor de {symbol} murió: {e} — reconcile lo relanzará")
        raise
```

En `watch_learner_scanner`, reemplazar:

```python
    for mint, pos in list(_auto_positions.items()):
        asyncio.create_task(_monitor_position(mint, pos["symbol"]))
```

por:

```python
    for mint, pos in list(_auto_positions.items()):
        _ensure_monitor(mint, pos.get("symbol", mint[:6]))

    asyncio.create_task(_reconcile_loop())
```

- [ ] **Step 4: Verificar que pasan**

Run: `python3 -m pytest tests/test_ensure_monitor.py -v`
Expected: 2 PASS

- [ ] **Step 5: Suite completa + commit**

Run: `python3 -m pytest tests/ -q`
Expected: `39 passed`

```bash
git add copytrade/learner_scanner.py tests/test_ensure_monitor.py
git commit -m "fix: monitores supervisados + reconciliación 60s — SL/TP siempre vigilado"
```

---

### Task 5: Apagar duplicados y relanzar limpio (operacional)

**Files:**
- Modify: ninguno (operaciones)

**Interfaces:**
- Consumes: el lock de Task 1 ya commiteado.

- [ ] **Step 1: Identificar y matar TODAS las instancias actuales**

```bash
pgrep -fl "Python main.py"
# Verificar con lsof que cada PID tiene cwd en botsolana antes de matar:
for PID in $(pgrep -f "Python main.py"); do lsof -p $PID 2>/dev/null | awk '$4=="cwd" {print "'$PID'", $NF}'; done
# Matar solo los que corren en /Users/branel/Desktop/botsolana:
kill <PIDs confirmados>
sleep 3
pgrep -fl "Python main.py"   # debe salir vacío (o solo procesos de OTROS proyectos)
```

- [ ] **Step 2: Relanzar UNA instancia**

```bash
cd /Users/branel/Desktop/botsolana && ./start_bot.sh
```

Expected: `✅ Bot arrancado correctamente con PID <n>`

- [ ] **Step 3: Verificar que el lock rechaza una segunda instancia**

```bash
cd /Users/branel/Desktop/botsolana && python3 main.py; echo "exit=$?"
```

Expected: `❌ Ya hay otra instancia de BotSolana corriendo (data/bot.lock). Saliendo.` y `exit=1`

- [ ] **Step 4: Verificar arranque sano en el log**

```bash
sleep 20 && tail -40 /tmp/botsolana_stable.log | grep -E "(learner|Scanner|monitor|ERROR)"
```

Expected: línea `🤖 Learner Scanner iniciado`, sin tracebacks.

---

## Criterio de éxito global (post-ejecución, 24–48h)

- Una sola instancia en `pgrep -fl "Python main.py"`.
- En `data/sim_history.json`, trades nuevos de `AUTONOMOUS_BOT` con `hold_min <= 6` y `pnl_pct >= -12` (SL -6% + fricción ≤ ~6%). Ninguna pérdida > -20%.
- Volumen de trades autónomos baja drásticamente (los filtros duros rechazan los buckets malos) — eso es lo esperado, no un bug.

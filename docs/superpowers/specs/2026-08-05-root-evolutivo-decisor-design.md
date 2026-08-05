# Diseño: ROOT como decisor evolutivo autónomo (sin reglas fijas)

**Fecha:** 2026-08-05
**Estado:** Aprobado
**Objetivo:** Que ROOT reemplace a la lógica de reglas como decisor de copytrade en BotSolana, aprendiendo continuamente de sus propios trades (ganados y perdidos) hasta converger en una configuración rentable — sin thresholds fijos, por prueba, análisis y mutación.

---

## Contexto y motivación

Hoy BotSolana tiene dos decisores corriendo:

- **Reglas** (`scorer.py` + `scoring_rules` en `learner_rules_auto.json`): decide de verdad qué wallet-buys se copian. Balance de papel $20 → $41,662 (identificado como artefacto de sizing por compounding, no ganancia real ejecutable). 1,219 trades reales acumulados desde el 26/06, 43.8% winrate global, 14 wallets.
- **ROOT / CopyScorer** (`root_shadow.py` + `root_sim.py`): regresión logística con pesos fijos entrenada offline una vez. Corre en shadow — solo observa y registra en paper-trading propio ($1000 inicial, $50/trade fijo). 13 trades cerrados, 46.2% winrate, +$716 — muestra chica, dominada por 1-2 ganadores grandes, no concluyente todavía.

El usuario pidió eliminar la lógica de reglas del camino de decisión por completo y que ROOT pase a decidir de verdad, aprendiendo sin reglas fijas — probando escenarios, analizando qué falló, ajustando y volviendo a probar hasta encontrar una configuración rentable. `LIVE_MODE=false` — todo esto corre 100% en simulación, sin dinero real en juego, lo cual habilita experimentar sin barandas de capital.

Alcance decidido: **solo BotSolana/copytrade** (Proyecto A). Expandir a otras áreas de trading fuera de memecoin queda para después, con lo aprendido acá.

---

## Arquitectura

### Antes
```
Wallet-buy detectado → scorer.py (reglas: scoring_rules) → decide COPIAR/SKIP → simulator.py
                              ↓ (paralelo, shadow, no decide nada)
                        root_shadow.py → CopyScorer (pesos fijos) → root_sim.py (paper aparte)
```

### Después
```
Wallet-buy detectado (sin cambios: sigue siendo la señal/trigger)
        ↓
   root_decider.py  ← usa la config "campeón" actual (pesos scorer + stop-loss% + max-hold + confianza por wallet)
        ↓ COPIAR
   root_sim.py (extendido) → abre posición de papel, loguea trayectoria de precio completa
        ↓ al cerrar
   resultado → data/root_sim_history.json (alimenta la próxima ronda evolutiva)
        ↓
   [ciclo evolutivo, corre aparte, cada N minutos o cada M trades nuevos]
   root_evolution.py:
     1. genera población de variantes (mutación de la config campeón + variación random)
     2. backtestea cada variante contra: 1,219 trades semilla + historial propio + trayectorias nuevas
     3. selecciona top candidatos → root_validation_pool.py (paper-trading paralelo, balance propio por candidato)
     4. cuando un candidato junta muestra mínima (20+ trades) y gana con margen robusto
        (incluso excluyendo su mejor trade), se promueve a campeón — reemplaza a root_decider.py
```

La lógica de reglas (`scoring_rules` como filtro de decisión) sale del camino de decisión real. Se retira su invocación desde `scorer.py`; el destino final del código (dejarlo inerte vs. borrarlo) se resuelve en el plan de implementación, no aquí.

---

## Componentes

### Archivos nuevos

**`copytrade/root_trajectory.py`**
- `record_snapshot(token_mint, ts, price)` — se llama desde el poll loop existente (cada 15s) mientras una posición está abierta.
- Persiste en `data/root_trajectories.jsonl`: `{token_mint, wallet, opened_at, snapshots: [[ts, price], ...]}`.
- Sin esto, el backtest engine no puede simular "qué hubiera pasado con otro stop-loss/hold-time" — es la pieza que falta hoy.

**`copytrade/root_backtest.py`**
- `backtest_config(config, dataset)` — función pura: dado un vector de config (pesos del scorer, stop_loss_pct, max_hold_min, trade_usd, pesos por wallet) y un dataset de trades históricos, devuelve pnl simulado agregado.
- Sobre trades con trayectoria completa (root_trajectories): puede re-simular exits alternativos de verdad.
- Sobre los 1,219 semilla y los 13 root_sim viejos (sin trayectoria): solo puede re-evaluar si esa config hubiera dicho COPIAR/SKIP, usando el pnl ya registrado — no simula exits alternativos ahí. Esta limitación queda documentada en el propio módulo para que nadie interprete el backtest sobre datos viejos como más preciso de lo que es.

**`copytrade/root_wallet_miner.py`**
- `wallet_features(history)` — por wallet: winrate, pnl promedio, timing de entrada relativo al pump (`change_Xh_pct` en el momento de compra), tamaño típico de posición, frecuencia de trades.
- Se recalcula periódicamente y se usa como feature de entrada al scorer (confianza por wallet), no como filtro fijo.

**`copytrade/root_evolution.py`**
- `Config` — dataclass: pesos del scorer, stop_loss_pct, max_hold_min, trade_usd, wallet_trust (dict).
- `mutate(config) -> Config` — variación aleatoria acotada sobre una config existente.
- `run_generation(population, dataset) -> population` — backtestea, selecciona top-K, muta para la siguiente generación.
- `evolution_loop()` — corre en thread daemon aparte, cadencia configurable por env var (`ROOT_EVOLUTION_INTERVAL_MIN`), nunca bloquea al bot. Cualquier excepción se loguea y se descarta — la config campeón sigue operando sin cambios si el ciclo falla.

**`copytrade/root_validation_pool.py`**
- Extiende el patrón de `root_sim.py` a N candidatos en paralelo, cada uno con su propio balance de papel (`data/root_validation_<config_id>.json`).
- `promote_if_ready(candidate, champion)` — criterio de promoción: muestra mínima 20+ trades, pnl agregado superior, y ventaja que se mantiene incluso excluyendo el mejor trade de cada lado (para no promover por 1 outlier de suerte).

**`copytrade/root_decider.py`**
- Reemplaza el rol de `root_shadow.py` como punto de entrada real desde `scorer.py`.
- `decide(wallet_label, entry_context, token_mint) -> "COPIAR" | "SKIP"` usando la config campeón vigente (`data/root_champion.json`).
- Mismo patrón de no-bloqueo y manejo de errores que el código existente (thread daemon, excepción se loguea y descarta, nunca tumba el camino real).

### Archivos modificados

**`copytrade/scorer.py`** — el call site que hoy invoca `scoring_rules` + dispara `shadow_score()` en paralelo pasa a invocar `root_decider.decide()` como la decisión real.

**`copytrade/root_sim.py`** — se extiende para loguear trayectoria (via `root_trajectory.record_snapshot`) durante el polling que ya existe, y para poder abrir posiciones bajo distintas configs (no solo la fija de siempre) cuando lo llama `root_validation_pool.py`.

### Datos

- **Semilla:** 1,219 trades históricos (`data/sim_history.json`, con `exit_context`/`entry_context` cuando existen) + 13 trades de `root_sim` — se cargan una vez al iniciar el backtest engine.
- **Nuevo:** cada trade que decide `root_decider.py` de acá en adelante se guarda con trayectoria completa, alimentando backtests cada vez más precisos.

---

## Manejo de errores

Mismo patrón ya establecido en el código existente: todo corre en threads daemon separados del camino de trading real, cualquier excepción se loguea con `log.warning`/`log.debug` y se descarta ahí mismo, nunca se propaga. El ciclo evolutivo y el pool de validación son fail-safe: si fallan, la config campeón sigue decidiendo sin cambios — nunca se cae a "no decidir nada".

## Testing

- `test_root_backtest.py` — `backtest_config()` con fixtures de trades históricos reales ya grabados (subset real de `sim_history.json`), no datos inventados. Verifica que reproduce el pnl esperado para la config actual y que cambia coherentemente con configs distintas.
- `test_root_evolution.py` — `mutate()` produce variantes dentro de los rangos esperados; `run_generation()` con una población y dataset sintético (pero basado en distribución real) selecciona correctamente el top-K.
- `test_root_validation_pool.py` — criterio de promoción: casos donde debe promover, casos donde no debe (muestra insuficiente, ventaja solo por 1 outlier).
- `test_root_trajectory.py` — snapshots se persisten y se pueden reconstruir la trayectoria completa de una posición.

Nada de mercado en vivo mockeado — todos los fixtures son datos reales ya grabados en `data/`.

---

## Fuera de alcance (Proyecto B, futuro)

Expandir el aprendizaje autónomo de ROOT a áreas de trading fuera de memecoin/copytrade (otros mercados, otras estrategias) — se aborda como proyecto separado una vez que este lazo tenga resultados reales que informen cómo hacerlo.

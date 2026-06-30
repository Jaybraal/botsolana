# Diseño: Modo Autónomo con Learner-Driven Scanner

**Fecha:** 2026-06-30  
**Estado:** Aprobado  
**Objetivo:** Que el bot opere sin copywallet — aprendiendo de los patrones de las wallets élite y encontrando tokens por sí mismo.

---

## Contexto y motivación

El `autonomous_scanner.py` actual tiene **2.9% WR en 34 trades**. El problema raíz: busca tokens de minutos en Pump.fun (sniping) pero las wallets élite que copia compran tokens con días de historia. Son estrategias opuestas.

Los datos ya están:
- `groq_patterns.json`: patrones de 2,487 trades reales de 8 wallets
- `stat_scorer.py`: calibrado con 2,659 trades
- `learner_rules.json`: scoring_rules derivadas de trades ganadores

Solo falta conectar esos datos a un scanner que busque los tokens correctos.

---

## Arquitectura

### Antes
```
PumpPortal WS → autonomous_scanner.py → stat_scorer → executor/simulator
               (tokens nuevos, 2.9% WR)

Wallets élite → watcher.py → simulator.py → learner.py
               (copywallet, 45% WR)        (aprende de todos los trades mezclados)
```

### Después
```
DexScreener scan ──┐
(cada 5 min)       ├──→ learner_scanner.py → stat_scorer + learner_rules → executor/simulator
PumpPortal API ────┘    (tokens 1-7 días)    (doble filtro)                      ↓
(validación precio)                                                   learner.update(source="AUTO")
                                                                               ↓
Wallets élite → watcher.py → simulator.py → learner.update(source="CW")
                                                  ↓
                                    learner_rules_copywallet.json ← lo que usa el scanner
                                    learner_rules_auto.json       ← evolución autónoma
```

---

## Componentes

### Archivos nuevos

**`copytrade/learner_scanner.py`**
- `scan_loop()` — loop principal cada 5 minutos
- `_scan_dexscreener()` — fetch trending/tokens con filtros de learner_rules
- `_validate_pumpportal(mint)` — confirma precio y actividad
- `_score_and_decide(token_info)` — doble filtro: stat_scorer + learner_rules
- `_open_position(mint, token_info)` — llama a execute_copy
- `_monitor_position(mint, symbol)` — SL/TP/trailing (mismo lógica que autonomous_scanner)
- `_recover_orphan_positions()` — recupera posiciones AUTO tras restart

### Archivos modificados

**`copytrade/learner.py`**
- Añadir campo `source: "CW" | "AUTO"` al registrar cada trade en `sim_history.json`
- Generar `data/learner_rules_copywallet.json` (solo trades source="CW")
- Generar `data/learner_rules_auto.json` (solo trades source="AUTO")
- El archivo `data/learner_rules.json` existente sigue generándose con todos los trades

**`main.py`**
- Añadir `watch_learner_scanner()` al `asyncio.gather()`

### Archivos sin cambios
- `simulator.py`, `executor.py`, `scorer.py`, `stat_scorer.py`, `watcher.py`
- `autonomous_scanner.py` — solo se deshabilita via `AUTO_MOMENTUM_BUYS=99999`

### Archivos generados
- `data/learner_rules_copywallet.json` — criterios de wallets élite
- `data/learner_rules_auto.json` — criterios autónomos (evoluciona con el tiempo)

---

## Flujo de datos completo

### Ciclo del scanner (cada 5 minutos)

**Paso 1 — Descubrimiento DexScreener**
```
get_trending_solana()          → /token-boosts/top/v1  (lista de mints boosteados en Solana)
get_tokens_batch(mints)        → /latest/dex/tokens/{mints}  (datos completos en batch)

Filtros de learner_rules_copywallet.json aplicados sobre pair data:
  • pairCreatedAt → age_days: 1–7
  • marketCap:     $15,571–$46,714
  • liquidity.usd: ≥ $3,127
  • volume.h24 / liquidity.usd (vol_liq_ratio): ≥ 4.0
  • txns.h1.buys / (txns.h1.buys + txns.h1.sells) (buy_pressure): ≥ 0.513
  • priceChange.h1: ≥ +78.98%
Resultado: lista de mints candidatos (típicamente 0–5 por ciclo)
```

**Paso 2 — Validación PumpPortal**
```
GET https://pumpportal.fun/api/coin-data?mint=...
Confirma: precio_usd > 0, actividad reciente
Descarta: precio = 0 o token sin datos
Añade al token_info: price_usd, price_sol
```

**Paso 3 — Doble filtro de scoring**
```
stat_scorer(token_info)       → score ≥ 55  (primera capa)
learner_rules_copywallet      → ≥ 5/7 criterios (segunda capa)
Ambos deben pasar → si no, descarta silenciosamente
```

**Paso 4 — Ejecutar compra**
```
execute_copy(buy_swap, wallet_label="AUTO 🤖")
simulator registra en sim_positions.json
monitor arranca en background
```

**Paso 5 — Monitor de posición (cada 10s)**
```
Fetch precio: DexScreener → PumpPortal fallback → last_price_usd
SL: pnl ≤ -8%         → venta
TP: pnl ≥ +25%        → venta
Trailing: pico ≥ +15% y caída ≥ 7% desde pico → venta
Timeout: hold ≥ 7 min → venta forzada
```

**Paso 6 — Al cerrar trade**
```
sim_history.json ← {source: "AUTO", won: bool, pnl_pct: float, entry_context: {...}}
learner.update() regenera learner_rules_auto.json
learner_rules_copywallet.json solo cambia cuando copywallet cierra trades
```

### Formato de trade en sim_history.json
```json
{
  "wallet_label": "AUTO 🤖",
  "source": "AUTO",
  "mint": "...",
  "won": true,
  "pnl_pct": 14.7,
  "entry_context": {
    "mcap_usd": 31000,
    "liquidity_usd": 6200,
    "vol_liq_ratio": 4.8,
    "buy_pressure": 0.61,
    "age_days": 2.3,
    "change_1h_pct": 95.0,
    "discovery_source": "dexscreener"
  }
}
```

### Loop de autoaprendizaje
```
Semana 1–2:  Scanner usa SOLO learner_rules_copywallet.json
             Acumula trades AUTO con source tag

Semana 3+:   Si AUTO WR ≥ 50% en ≥ 100 trades:
             learner_rules_auto.json se blendea 30% en criterios
             El scanner desarrolla su propio fingerprint

Mes 2+:      AUTO tiene criterios calibrados propios
             Copywallet se puede reducir gradualmente
```

---

## Manejo de errores

| Situación | Comportamiento |
|-----------|---------------|
| DexScreener rate limit / falla | log warning, esperar 30s, reintentar. No crashear. |
| PumpPortal no responde | Proceder solo con datos DexScreener. Si precio=0, descartar candidato. |
| 0 candidatos en ciclo | Normal — solo log debug. Log informativo cada 30 min. |
| Posiciones huérfanas al arrancar | `_recover_orphan_positions()` lee sim_positions.json y rearma monitors. |
| `learner_rules_copywallet.json` no existe | Fallback a criterios hardcoded: mcap 15k–47k, vol/liq > 4, age_days < 7.3 |
| 3 fallos consecutivos de APIs | Log error claro, continuar intentando (no detener el bot) |

---

## Testing y criterio de éxito

### Fase 1 — SIM paralelo (2 semanas)
- Copywallet corre igual que hoy
- `learner_scanner` corre con `MAX_AUTO_POSITIONS=2`
- Monitor: `grep "AUTO.*WIN\|AUTO.*LOSS\|RESUMEN" logs/simulator_*.log`

### Criterio de graduación
```
≥ 100 trades AUTO cerrados     → estadísticamente válido
WR ≥ 50%                       → comparable a copywallet
Profit factor ≥ 1.2            → ganancia media > pérdida media
```

### Si pasa
- Reducir copywallet de 7 wallets a 4 (Decu, Cented, Cupsey, Theo)
- Subir `MAX_AUTO_POSITIONS` de 2 a 3
- Re-evaluar en 2 semanas

### Si no pasa
- Ajustar un criterio de `learner_rules_copywallet.json` a la vez
- No tocar `stat_scorer.py` ni los parámetros SL/TP

### Variables de entorno nuevas
```
LEARNER_SCANNER_ENABLED=true       # activa/desactiva el nuevo scanner
LEARNER_SCAN_INTERVAL_MIN=5        # frecuencia de scan DexScreener
MAX_AUTO_POSITIONS=2               # máximo posiciones autónomas simultáneas
LEARNER_SCORE_THRESHOLD=55         # threshold stat_scorer para el auto
LEARNER_CRITERIA_MATCH=5           # criterios de learner_rules que deben coincidir (de 7)
AUTO_MOMENTUM_BUYS=99999           # deshabilita el autonomous_scanner viejo
```

---

## Variables en Railway a actualizar
```
AUTO_MOMENTUM_BUYS=99999
LEARNER_SCANNER_ENABLED=true
LEARNER_SCAN_INTERVAL_MIN=5
MAX_AUTO_POSITIONS=2
```

---

## Decisiones de diseño

1. **Separar learner por source** en vez de crear un learner nuevo — mínimo impacto en código existente
2. **Mantener autonomous_scanner.py** activo pero deshabilitado — evita cambios en main.py más allá de añadir una línea
3. **DexScreener como descubridor, PumpPortal como validador** — DexScreener tiene historial de días, PumpPortal tiene precio en tiempo real
4. **Doble filtro** (stat_scorer + learner_rules) — stat_scorer está calibrado con 2,659 trades, learner_rules añade criterios específicos de ganadores. Juntos reducen falsos positivos.
5. **Criterios hardcoded como fallback** — el sistema funciona desde el primer arranque sin depender de que learner_rules_copywallet.json ya exista

# BotSolana — Pivot a Sniper Autónomo
**Fecha:** 2026-06-26  
**Estado:** Diseño aprobado — listo para implementación

---

## Contexto

El bot estaba pausado por tres problemas confirmados:
1. Railway inestable (filesystem efímero, caídas sin aviso)
2. Modo autónomo con scorer hardcodeado que no funciona
3. Copy wallet en live llega 1-3s tarde → pérdida estructural

El análisis de los 4,913 trades reales en `wallet_history.db` confirmó:
- Las wallets élite (Theo 89.3%, Nyhrox 87.1%, Cupsey 84.9%, Decu 84.4%) son genuinamente buenas
- La zona de oro es **tokens de 1-3 min de edad con mcap 30-70k** (90.6% WR validado)
- RC (0% WR, 154 trades todos UNKNOWN) y Trey (48.6% WR) deben eliminarse
- El problema no es la estrategia — es que llegamos tarde al copiar

**La solución:** convertirnos en el sniper, no en el copiador tardío.

---

## Sección 1: Arquitectura

```
ANTES (roto):
  Wallet trade → detectar (Helius/PP) → copiar → llegar tarde → pérdida

AHORA:
  Pump.fun: token nuevo
       ↓
  autonomous_scanner detecta (PumpPortal WS subscribeNewToken)
       ↓
  snipe_scorer evalúa con datos reales de 4,913 trades
  → edad 1-3 min? mcap 30-70k? momentum?
       ↓
  boost si Theo/Nyhrox/Cupsey/Decu también compraron (señal secundaria)
       ↓
  BUY si score >= 55
       ↓
  gestionar posición: SL/TP/trailing (autonomous_scanner existente)
```

**Lo que se reutiliza sin cambios:**
- `copytrade/autonomous_scanner.py` — lógica de detección y gestión de posición
- `copytrade/executor.py` — compras/ventas reales y SIM
- `copytrade/simulator.py` — simulador realista con fees y slippage
- `copytrade/watcher.py` — copy trading como señal secundaria (boost de score)

**Lo que se construye:**
- `data_collector/snipe_trainer.py` — lee DB, deriva patrones, escribe `snipe_patterns.json`
- `copytrade/snipe_scorer.py` — evalúa tokens en tiempo real usando esos patrones

**Lo que se reemplaza:**
- `copytrade/stat_scorer.py` → reemplazado por `snipe_scorer.py` (data-driven, no hardcodeado)

**Lo que se elimina de TARGET_WALLETS:**
- RC (0% WR, todos UNKNOWN — datos inútiles)
- Trey (48.6% WR — por debajo del azar)

---

## Sección 2: snipe_trainer.py

**Ubicación:** `data_collector/snipe_trainer.py`  
**Entrada:** `data/wallet_history.db` (trades con outcome WIN/LOSS)  
**Salida:** `data/snipe_patterns.json`

### Lógica

Lee todos los trades donde `outcome IN ('WIN', 'LOSS')` (2,487 trades con resultado conocido).
Extrae features disponibles en el momento del snipe:

- `token_age_min` — edad al comprar
- `mcap` — market cap en la bonding curve
- `buys` — número de compras acumuladas (momentum)

Calcula WR + avg_pnl por bucket de cada feature. El scorer los lee en tiempo real.

### Estructura de snipe_patterns.json

```json
{
  "generated_at": "2026-06-26T...",
  "total_trades": 2487,
  "age_buckets": [
    {"label": "<1min",   "min": 0,  "max": 1,  "wr": 79.1, "n": 829,  "score_pts": 20},
    {"label": "1-3min",  "min": 1,  "max": 3,  "wr": 90.6, "n": 287,  "score_pts": 35},
    {"label": "3-10min", "min": 3,  "max": 10, "wr": 87.3, "n": 157,  "score_pts": 25},
    {"label": "10-30min","min": 10, "max": 30, "wr": 81.6, "n": 158,  "score_pts": 15},
    {"label": "30+min",  "min": 30, "max": 999,"wr": 78.9, "n": 147,  "score_pts": 5}
  ],
  "mcap_buckets": [
    {"label": "<10k",   "min": 0,     "max": 10000, "wr": 81.6, "score_pts": 15},
    {"label": "10-30k", "min": 10000, "max": 30000, "wr": 81.9, "score_pts": 20},
    {"label": "30-70k", "min": 30000, "max": 70000, "wr": 91.3, "score_pts": 30},
    {"label": "70-150k","min": 70000, "max": 150000,"wr": 87.5, "score_pts": 20},
    {"label": "150k+",  "min": 150000,"max": 999999,"wr": 74.5, "score_pts": 5}
  ],
  "momentum_buckets": [
    {"label": "50-150 buys", "min": 50,  "max": 150, "wr": 90.5, "score_pts": 20},
    {"label": "300+ buys",   "min": 300, "max": 9999,"wr": 87.5, "score_pts": 15}
  ],
  "elite_wallet_boost": 15,
  "buy_threshold": 55
}
```

---

## Sección 3: snipe_scorer.py

**Ubicación:** `copytrade/snipe_scorer.py`  
**Contrato:** mismo que `stat_scorer.py` — `score_token(token_info) -> (score, passed, reason)`

### Lógica de scoring

```
score = 0

1. Edad del token (feature más importante — +35 pts max)
   → lookup en age_buckets por token_age_min

2. Market cap (zona de oro 30-70k — +30 pts max)
   → lookup en mcap_buckets por mcap

3. Momentum (buys acumulados — +20 pts max)
   → lookup en momentum_buckets por buys

4. Señal de wallet élite (Theo/Nyhrox/Cupsey/Decu compraron este token)
   → +15 pts si alguna wallet élite ya compró

score máximo teórico: 100
umbral de compra: 55
```

### Carga de patrones

Carga `snipe_patterns.json` al inicializar. Si el archivo no existe, lanza un error claro pidiendo ejecutar `snipe_trainer.py` primero. No usa fallbacks hardcodeados.

---

## Sección 4: Cambios de configuración

### Variables Railway / .env a cambiar

```bash
AUTONOMOUS_MODE=true          # era false
AUTO_EVAL_DELAY_MIN=1         # era 6 — entrar en ventana 1-3 min
AUTO_MOMENTUM_BUYS=40         # era 150 — umbral realista
AUTO_MAX_HOLD_MIN=8           # era 12
AUTO_STOP_LOSS_PCT=-20        # era -15
AUTO_TAKE_PROFIT_PCT=80       # era 40 — wallets hacen 2x+
USE_GROQ_SCORER=false         # ya está, mantener
SNIPE_MODE=true               # nuevo flag para usar snipe_scorer
```

### TARGET_WALLETS — eliminar RC y Trey

```
Mantener: Theo, Nyhrox, Cupsey, Decu, Cupsey-2, Cented, Domy
Eliminar: RC (0% WR), Trey (48.6% WR)
```

---

## Sección 5: Infraestructura local (sin costo)

**Herramienta:** `tmux` — sesión persistente que sobrevive cierre de terminal

```bash
# Primera vez
brew install tmux    # si no está instalado

# Arrancar el bot
tmux new -s botsolana
cd ~/Desktop/botsolana
python3 main.py

# Ctrl+B, D → desconectar (bot sigue corriendo)

# Ver qué hace
tmux attach -t botsolana

# Prevenir que el Mac duerma
# Preferencias del Sistema → Batería → "Never" sleep mientras está enchufado
```

**La data persiste** en `~/Desktop/botsolana/data/` — sin resets, sin Railway Volume.

**Cuando el bot demuestre rendimiento real en SIM:** ahí se justifica el VPS ($4-6/mes) y se activa live mode con $200+.

---

## Sección 6: Plan de validación

### Criterios para pasar a live mode

| Métrica | Mínimo para live |
|---|---|
| WR en SIM | > 60% (neto, con fees y slippage) |
| Profit Factor | > 1.4 |
| Trades mínimos | ≥ 50 en la nueva configuración |
| Drawdown máximo | < 25% del capital |
| Días de SIM | ≥ 7 días continuos |

### Comando de monitoreo

```bash
# Resumen de rendimiento
tmux attach -t botsolana
# o desde otra terminal:
grep -E "(WIN|LOSS|RESUMEN|scorer|OPEN|CLOSE)" ~/Desktop/botsolana/logs/bot.log | tail -50
```

### Orden de ejecución

1. Ejecutar `snipe_trainer.py` → genera `snipe_patterns.json`
2. Verificar output del trainer (WR por bucket, sample sizes)
3. Activar en SIM con la nueva config
4. Monitorear 7 días
5. Si criterios de live se cumplen → activar con $200 mínimo

---

## Resumen de archivos nuevos / modificados

| Archivo | Acción | Descripción |
|---|---|---|
| `data_collector/snipe_trainer.py` | **NUEVO** | Lee DB, deriva patrones, escribe JSON |
| `copytrade/snipe_scorer.py` | **NUEVO** | Scorer data-driven, reemplaza stat_scorer |
| `config.py` | **MODIFICAR** | Flag SNIPE_MODE, quitar RC/Trey de defaults |
| `.env` / Railway | **MODIFICAR** | Parámetros autónomo corregidos |
| `data/snipe_patterns.json` | **GENERADO** | Output del trainer |

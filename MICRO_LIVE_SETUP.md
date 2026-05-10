# 🚀 MICRO-LIVE SETUP — $20-50 Reales

**Status**: 🟢 LISTO PARA INICIAR
**Fecha**: 10 de Mayo 2026

---

## 📋 CONFIGURACIÓN APLICADA

### ✅ Cambios Implementados

```python
# 1. SIZING AJUSTADO
MAX_TRADE_PCT = 3.5%  (vs 2.0% anterior, 5.0% histórico)

# 2. WEIGHTED ALLOCATION
Cupsey-2:  40% → tamaño efectivo = 1.4%
Decu:      30% → tamaño efectivo = 1.05%
Cented:    20% → tamaño efectivo = 0.7%
Cupsey:    10% → tamaño efectivo = 0.35%

# 3. SEGURIDAD INTEGRADA
- Circuit breaker: Detiene si pierde >30% en 1h
- Max capital: $100 USD
- Emergency stop: CTRL+C mata inmediatamente
- Logging detallado de cada tx
```

---

## 🎯 OBJETIVO DEL MICRO-LIVE

| Pregunta | Respuesta |
|----------|-----------|
| ¿Cuánto capital? | $20-50 USD reales |
| ¿Duración? | 24-48 horas |
| ¿Qué medir? | Live ROI vs Simulador |
| ¿Stop condition? | >30% loss en 1h O 48h completadas |

---

## 🔧 ANTES DE INICIAR

### 1. Verificar Wallet Setup

```bash
# Verificar que tienes .env configurado
cat .env | grep WALLET_PUBKEY
cat .env | grep WALLET_PRIVKEY_B58

# Si no:
echo "WALLET_PUBKEY=<tu_wallet>" >> .env
echo "WALLET_PRIVKEY_B58=<tu_privkey_base58>" >> .env
```

### 2. Verificar Capital Disponible

```python
# Mínimo: $20 USD en Solana wallet
# Máximo para micro-live: $100

# Verificar balance:
solana balance <tu_wallet>
```

### 3. Enable Live Mode en Railway

```bash
# En Railway UI o via CLI:
railway env:add LIVE_MODE=true

# Verificar:
railway env:list | grep LIVE_MODE
```

---

## 📊 ESTRUCTURA DEL TEST

### Fase 1: Inicialización (5 min)

```python
from live_micro import create_micro_live_session

trader = create_micro_live_session(capital_usd=25.0)
# ✅ Sesión lista
# 📊 Weighted allocation: ON
# 🛡️  Safety: ON
```

### Fase 2: Trading (24-48h)

- Bot copia wallets automáticamente
- Registra cada tx en `data/live_micro_session.jsonl`
- Circuit breaker monitorea losses
- Logs en tiempo real

### Fase 3: Comparación

```bash
python3 compare_live_vs_sim.py

# Output:
# ✅ VIABLE: Live ROI (2500%) está en rango esperado (500-5000%)
# o
# ❌ NEGATIVE: Live ROI (-15%) no confirma edge
```

---

## 📈 BENCHMARKS

Lo que esperamos ver:

| Simulador | ROI | Status |
|-----------|-----|--------|
| Railway (sin fricción) | +45,325% | 🔴 FAKE — over-optimistic |
| Con fricción realista | -31.4% | 🔴 TOO CONSERVATIVE |
| **LIVE (esperado)** | **+500% a +5000%** | 🟢 **REALISTIC** |

---

## 🚨 SECURITY FEATURES

### Circuit Breaker Automático

```python
# Si pierdes >30% en 1 hora → STOP AUTOMÁTICO
if elapsed_hours <= 1.0 and loss_pct < -0.30:
    log.error("🚨 EMERGENCY STOP ACTIVATED")
    # Bot se detiene
    # Sesión se preserva para análisis
```

### Emergency Stop Manual

```bash
# CTRL+C en cualquier momento detiene gracefully
# ✅ Cierra posiciones abiertas
# ✅ Exporta datos para análisis
# ✅ Logging completo
```

### Capital Limit

```python
# Máximo inicial: $100
# Mínimo inicial: $1
if capital_usd > 100.0:
    raise ValueError("Capital máximo para micro-live: $100.0")
```

---

## 📊 ARCHIVOS DE LOGGING

### Session Log (JSONL)

```
data/live_micro_session.jsonl

Cada línea:
{
  "timestamp": "2026-05-10T14:30:45.123456",
  "wallet": "Cupsey-2",
  "token": "MEME",
  "side": "buy",
  "amount_usd": 0.7,
  "price": 0.000125,
  "pnl_pct": null,
  "status": "success",
  "balance": 25.34
}
```

### Comparison Report

```
data/comparison_report_20260510_143045.json

{
  "timestamp": "2026-05-10T14:30:45",
  "comparison": {
    "railway_roi_pct": 45325.3,
    "realistic_roi_pct": -31.4,
    "live_roi_pct": 2845.5,
    "live_win_rate": 0.56,
    "live_trades": 127
  },
  "status": "VIABLE_CONFIRMED"
}
```

---

## 🎯 COMANDOS PARA INICIAR

### 1. Test Session (sin dinero real)

```bash
# Simular 24h primero
python3 test_new_params.py

# Output:
# ✅ Test completo
# 📊 Resultados en data/test_results_*.json
```

### 2. Micro-Live Real

```bash
# IMPORTANTE: Verificar .env antes
cat .env | grep LIVE_MODE

# Iniciar sesión ($25 USD)
python3 -c "
from live_micro import create_micro_live_session
trader = create_micro_live_session(capital_usd=25.0)
trader.print_summary()
"

# Bot está corriendo — logs en:
# - data/live_micro_session.jsonl (JSONL)
# - Stdout (en tiempo real)
```

### 3. Monitorear En Vivo

```bash
# En otra terminal:
tail -f data/live_micro_session.jsonl | jq '.'

# O comparar periódicamente:
python3 compare_live_vs_sim.py
```

### 4. Al Finalizar

```bash
# Generar reporte final
python3 compare_live_vs_sim.py

# Salida:
# ✅ comparison_report_*.json generado
# 📊 Estado: VIABLE_CONFIRMED o NEGATIVE_NEEDS_DEBUG
```

---

## 🚀 PRÓXIMO PASO

Una vez completado el micro-live (24-48h):

1. **Si ROI está en +500% a +5000%**
   → ✅ Edge confirmado
   → Escalar a $100-500 reales
   → Ir a live trading en producción

2. **Si ROI está fuera del rango**
   → 🔴 Debug parámetros
   → Verificar suposiciones
   → Iterar test suite

---

## 📝 NOTAS IMPORTANTES

1. **No usar todo tu dinero**
   - Máximo $25-50 USD
   - Esto es testing, no producción

2. **Monitorear activamente**
   - Revisar logs cada 2-4 horas
   - Buscar anomalías

3. **Preservar datos**
   - No borres `data/live_micro_session.jsonl`
   - Necesario para análisis post-trading

4. **Paciencia**
   - 24h mínimo para estadísticas significativas
   - No juzgues en 2 horas

---

**Estado**: 🟢 LISTO PARA INICIAR

¿Quieres que inicie el micro-live ahora con $25 USD?


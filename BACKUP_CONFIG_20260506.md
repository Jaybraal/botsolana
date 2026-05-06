# 💾 BACKUP CONFIGURACIÓN — 6 Mayo 2026 21:35 UTC

## Estado antes de cambios

**Commit actual:** `a1bceaa` (cooldown 2 min)

**Fecha:** 6 de Mayo 2026 21:35 UTC

**Razón del backup:** Antes de implementar latency delay simulation y mejoras en filtros

---

## Railway Environment Variables (ACTUAL)

```
LIVE_MODE=false
SIM_CAPITAL=50
SIM_RESET=false
SIM_SLIPPAGE_PCT=0.015
SIM_PRIORITY_FEE_SOL=0.0004
SIM_MAX_TRADE_USD=5
SIM_TRADE_PCT=0.10
MAX_TRADE_PCT=0.10
MIN_TRADE_SOL=0.001
MAX_OPEN_COPIES=3
MAX_SESSION_LOSS_PCT=0.20
SLIPPAGE_BPS=50
PROPORTIONAL_MODE=true
MIN_PROFIT_USD=0.5
TRADE_AMOUNT_USD=50

SOLANA_RPC_HTTP=https://api.mainnet-beta.solana.com
SOLANA_RPC_WS=wss://api.mainnet-beta.solana.com

TARGET_WALLETS=CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o,3LUfv2u5yzsDtUzPdsSJ7ygPBuqwfycMkjpNreRR2Yww,Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt,2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f,6S8GezkxYUfZy9JPtYnanbcZTMB87Wjt1qx3c6ELajKC,4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk,4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9,DuQabFqdC9eeBULVa7TTdZYxe8vK8ct5DZr4Xcf7docy,7SDs3PjT2mswKQ7Zo4FTucn9gJdtuW4jaacPA65BseHS,suqh5sHtr8HyJ7q8scBimULPkPpA557prMG47xCHQfK,831yhv67QpKqLBJjbmw2xoDUeeFHGUx8RnuRj9imeoEs

WALLET_PUBKEY=F9kYAERneG7Qo9ZRrNBQ3pjfqiiv9FaTenMKwVTG9zaG
WALLET_PRIVKEY_B58=***REDACTED***
```

---

## Commits clave (estado actual)

```
a1bceaa — Cooldown 2 min (evita trades rápidos)
d769fe7 — Circuit breaker 20% loss
61a092d — Protecciones 1-3 (failed attempts, price impact, liquidez)
4722114 — Multi-backend fallback
```

---

## Métricas actuales (SIMULACIÓN)

```
Capital inicial:    $50
Últimas ganancias:  5 trades | 100% win rate | +93% ROI
Win rate:           100%
Balance actual:     ~$96.61
Problemas:          SIMULACIÓN IRREAL (no considera latency)
```

---

## Cómo revertir si algo falla

### Opción 1: Revertir a este commit exacto
```bash
git log --oneline | head -20                    # Ver últimos commits
git reset --hard a1bceaa                        # Volver a este commit
git push -f origin main                         # Forzar push a GitHub
railway variables set DEPLOY_TS=$(date +%s)    # Redeploy en Railway
```

### Opción 2: Restaurar variables de Railway
```bash
# Estos son los valores actuales — si los nuevos fallan, restaurar:

railway variables set LIVE_MODE=false
railway variables set SIM_CAPITAL=50
railway variables set SIM_SLIPPAGE_PCT=0.015
railway variables set SIM_PRIORITY_FEE_SOL=0.0004
railway variables set MAX_SESSION_LOSS_PCT=0.20
railway variables set MAX_TRADE_PCT=0.10
railway variables set MIN_TRADE_SOL=0.001
railway variables set MAX_OPEN_COPIES=3
railway variables set DEPLOY_TS=$(date +%s)
```

---

## Archivos críticos (snapshot)

### config.py (líneas clave)
```python
SLIPPAGE_BPS     = int(os.getenv("SLIPPAGE_BPS", "75"))
MAX_TRADE_PCT    = float(os.getenv("MAX_TRADE_PCT",  "0.05"))
MIN_TRADE_SOL    = float(os.getenv("MIN_TRADE_SOL",  "0.005"))
MAX_OPEN_COPIES  = int(os.getenv("MAX_OPEN_COPIES", "999"))
STOP_LOSS_PCT    = float(os.getenv("STOP_LOSS_PCT",  "0.70"))
MAX_SESSION_LOSS_PCT = float(os.getenv("MAX_SESSION_LOSS_PCT", "0.20"))
MAX_PRICE_IMPACT = float(os.getenv("MAX_PRICE_IMPACT", "2.0"))
```

### simulator.py (líneas clave de cálculo)
```python
# Línea 393-394: Slippage bidireccional
entry_adj     = entry * (1 + SIM_SLIPPAGE_PCT)
exit_adj      = price_exit * (1 - SIM_SLIPPAGE_PCT)
pnl_pct       = (exit_adj - entry_adj) / entry_adj * 100

# Línea 399-400: Fees
fee_usd  = SIM_PRIORITY_FEE_SOL * _get_sol_price_usd()
pnl_usd -= fee_usd

# Línea 376-377: Hold time (para logs)
hold_sec = time.time() - pos["opened_at"]
hold_min = hold_sec / 60
```

### executor.py (protecciones actuales)
```python
# Línea 333-337: Cooldown
last_sell_time = _recent_sells.get(token_out, 0)
if last_sell_time and (time.time() - last_sell_time) < 120:
    log.debug(f"[{label}] {swap['symbol_out']} vendido hace {time.time() - last_sell_time:.0f}s — cooldown activo")
    return False

# Línea 327-329: Failed attempts
if _failed_buy_attempts.get(token_out, 0) >= 2:
    log.debug(f"[{label}] {swap['symbol_out']} ya falló 2 veces — ignorando para ahorrar fees")
    return False

# Línea 335-337: Liquidez mínima
if _pair_info and _liquidity_usd < 500:
    log.warning(f"[{label}] Liquidez ${_liquidity_usd:.0f} < $500 — abortando")
    return False

# Línea 379-384: Price impact
if _pre_quote and calc_price_impact(_pre_quote) > MAX_PRICE_IMPACT:
    log.warning(f"[{label}] Price impact {calc_price_impact(_pre_quote):.2f}% > {MAX_PRICE_IMPACT}%")
    return False
```

---

## Lo que va a cambiar (próximas acciones)

### CAMBIO 1: Latency Delay Simulation
**Dónde:** `copytrade/simulator.py` línea 296-310

**Antes:**
```python
opened_at = wallet_buy_time if wallet_buy_time else detected_at
latency_s = detected_at - opened_at if wallet_buy_time else 0
```

**Después será:**
```python
# Simular que el precio sube mientras tú esperas (delay de 1.5s)
latency_s = 1.5  # segundos reales de latencia
price_change_per_sec = 0.02  # 2% por segundo en BC
entry_latency_penalty = latency_s * price_change_per_sec
# Esto va a bajar la ganancia simulada
```

**Impacto esperado:**
- Win rate: 100% → 60-70% (REALISTA)
- ROI: +93% → ~+15-25% (REALISTA)

---

### CAMBIO 2: Aumentar mínimos de filtro
**Dónde:** `copytrade/executor.py` línea 335

**Antes:**
```python
if _pair_info and _liquidity_usd < 500:
```

**Después será:**
```python
if _pair_info and _liquidity_usd < 5000:  # $5k mínimo, no $500
```

---

### CAMBIO 3: Bajar price impact permitido
**Dónde:** `config.py` línea 65

**Antes:**
```python
MAX_PRICE_IMPACT = float(os.getenv("MAX_PRICE_IMPACT", "2.0"))  # 2%
```

**Después será:**
```python
MAX_PRICE_IMPACT = float(os.getenv("MAX_PRICE_IMPACT", "0.15"))  # 15%
```

Railway variables:
```bash
railway variables set MAX_PRICE_IMPACT=0.15
```

---

## Checklist si algo falla

```
❌ Win rate bajó demasiado (<40%)
   → Revertir latency delay
   → Volver a a1bceaa

❌ Capital empezó a perder mucho
   → Revisar SIM_SLIPPAGE_PCT (subir a 0.02)
   → O revertir completamente

❌ Circuit breaker se dispara constantemente
   → Aumentar MAX_SESSION_LOSS_PCT a 0.30
   → O revertir a 0.20

✅ Win rate estable 60-70%
   → Proceder con LIVE (si capital > $200)

✅ Capital creciendo consistentemente
   → Validar por 1-2 semanas más
   → Luego activar LIVE
```

---

## Cómo restaurar si todo falla

**OPCIÓN RÁPIDA (5 minutos):**
```bash
git reset --hard a1bceaa
git push -f origin main
railway variables set DEPLOY_TS=$(date +%s)
# Esperar 30s a que redeploy termine
```

**OPCIÓN LENTA (revisar todo primero):**
```bash
git log --oneline | grep -E "a1bceaa|d769fe7|61a092d"  # Ver commits
git show a1bceaa                                        # Ver qué cambió
git reset --hard a1bceaa                                # Si OK
git push -f origin main                                 # Si OK
railway variables set DEPLOY_TS=$(date +%s)            # Deploy
```

---

## Notas importantes

- **No toques WALLET_PRIVKEY_B58** — es tu clave real
- **SIM_RESET=false** — esto mantiene el progreso, NO resetea cada vez
- **LIVE_MODE=false** — dinero TEÓRICO, no real
- **Guarda este archivo** — es tu punto de restauración

---

## Próximas acciones (confirmadas)

1. ⏸️ **PAUSA AQUÍ** — el usuario quiere guardar configuración primero ✓
2. 🔄 **IMPLEMENTAR CAMBIOS** — cuando el usuario diga "procede"
3. 📊 **MONITOREAR 2 SEMANAS** — ver si ROI baja a número realista
4. ✅ **SI FUNCIONA** — entonces activamos LIVE con $200+
5. ❌ **SI NO FUNCIONA** — revertimos a a1bceaa (este backup)

---

**Backup completado:** 6 mayo 2026 21:35 UTC
**Commit a restaurar si falla:** `a1bceaa`
**Todos los archivos:** GitHub (`git reset --hard a1bceaa`)

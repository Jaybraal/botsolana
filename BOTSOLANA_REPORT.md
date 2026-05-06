# 📊 BotSolana — Reporte Técnico Completo

## 1. QUÉ ES EL BOT (Concepto)

**Copy Trading Bot** — Replica automáticamente los trades de wallets profesionales en Solana.

**Idea:** 
- Monitoreas 11 wallets que son traders profesionales
- Cuando hacen un trade (compran un token), el bot hace exactamente lo mismo
- Cuando venden, el bot vende
- Objetivo: Copiar su win rate (64% ganancias)

**Ventaja:**
- No necesitas predecir el mercado
- Dejas que profesionales hagan el trabajo
- Tú ganas si ellos ganan

---

## 2. CÓMO FUNCIONA (Flujo de datos)

```
BLOCKCHAIN (Solana)
       ↓
11 WALLETS MONITOREADAS (profesionales)
       ↓
[WATCHER] — Detecta transacciones en tiempo real
       ↓
[EXECUTOR] — Ejecuta la compra/venta en TU wallet
       ↓
[SIMULATOR] — Calcula P&L (ganancias/pérdidas)
       ↓
LOGS + REPORTE (ves lo que pasó)
```

### Flujo detallado de UN trade:

```
1. Theo compra 5 SOL → Token "ABC" en Pump.fun
   ↓ [Detectado por WATCHER en 1-3 segundos]
   
2. EXECUTOR calcula:
   - Theo invirtió 5 SOL = 10% de su capital
   - Tú tienes $500 = 10% = $50
   - EXECUTOR: Compra $50 de ABC por ti
   
3. SIMULATOR registra:
   - Entrada: $50 a precio X
   - Fees y slippage deducidos
   - Balance actualizado
   
4. THEO VENDE ABC → SOL (gana 20%)
   ↓ [Detectado]
   
5. EXECUTOR vende TODO tu ABC → SOL
   
6. SIMULATOR calcula:
   - Salida: $60 (ganancia +20%)
   - Menos fees (-$0.036)
   - Menos slippage (-$1.50)
   - Balance final: $508.46 (+$8.46)
```

---

## 3. STACK TÉCNICO

### Backend (Bot en sí)
| Componente | Tecnología | Función |
|---|---|---|
| **Lenguaje** | Python 3.12 | Lógica principal |
| **RPC** | Helius + api.mainnet-beta | Conexión a Solana |
| **WebSocket** | Helius WS + PumpPortal WS | Detección de transacciones en vivo |
| **Ruteo** | Jupiter API v6 | Encontrar mejores rutas de swap |
| **DeFi** | Pump.fun + PumpPortal | Bonding curve y swaps |
| **Datos** | DexScreener API | Precios y liquidez de tokens |

### Deploy
| Servicio | Propósito |
|---|---|
| **Railway** | Servidor 24/7 (corre el bot permanentemente) |
| **GitHub** | Control de versión + almacenar código |
| **Environment vars** | Guardar wallet keys, configuración segura |

### Librerías Python
```python
solders          # Construir transacciones Solana
solana-py        # Cliente RPC para blockchain
websockets       # Detectar eventos en vivo
httpx            # Hacer requests HTTP a APIs
pydantic         # Validar datos
rich             # Mostrar logs bonitos
```

---

## 4. ARQUITECTURA (Módulos principales)

### `/copytrade/watcher.py` — Detecta transacciones
- Se conecta a Helius WebSocket
- Se conecta a PumpPortal WebSocket
- Escucha TODOS los swaps en Solana
- Filtra: Solo los de tus 11 wallets
- **Output:** Diccionario con detalles del swap (token_in, token_out, amount, etc.)

### `/copytrade/executor.py` — Ejecuta trades
- Recibe el swap de Watcher
- Calcula cuánto invertir (proporcional al % que invirtió Theo)
- **En SIMULACIÓN:** Calcula P&L teórico
- **En LIVE:** Construye TX real → firma con tu private key → envía a blockchain
- Soporta 3 rutas:
  1. **Pump.fun BC** (PumpPortal/PumpAPI)
  2. **PumpSwap AMM** (fallback)
  3. **Jupiter** (DEX routing estándar)

### `/copytrade/simulator.py` — Calcula ganancias
- Simula EXACTAMENTE lo que pasaría en live
- Aplica slippage real (1.5% por operación)
- Aplica fees reales (priority fee en SOL)
- Compone el capital (ganancias generan más ganancias)
- **Output:** Balance actualizado, Win rate, ROI

### `/utils/jupiter.py` — Integración Jupiter
- Pide quotes a Jupiter (precio de swaps)
- Construye transacciones
- Calcula price impact
- **Fallback robusto:** Si falla, intenta otro backend

### `/utils/pumpfun.py` — Integración Pump.fun
- **3 backends en cascada:**
  1. PumpPortal (primario)
  2. PumpAPI.fun (alternativa)
  3. Jupiter on-chain (fallback final)
- Construye TX de bonding curve

### `/utils/dexscreener.py` — Datos de mercado
- Obtiene precio actual de tokens
- Obtiene liquidez USD disponible
- **Validación:** No compra si liquidez < $500

### `/config.py` — Configuración centralizada
- 60+ variables (wallet, RPC, slippage, stop-loss, etc.)
- Se lee de `.env` (variables de Railway)
- Una sola fuente de verdad

---

## 5. LAS 4 PROTECCIONES CONTRA PÉRDIDAS

| Protección | Dónde | Qué hace |
|---|---|---|
| **1. Failed attempts** | executor.py L327 | Si token falla 2x, se ignora |
| **2. Price impact** | executor.py L379 | Si impacto >50%, se aborta TX |
| **3. Liquidez mínima** | executor.py L335 | Si liquidez <$500, no compra |
| **4. Cooldown 2 min** | executor.py L333 | Si token se vendió <2 min atrás, no recompra |

---

## 6. VARIABLES DE CONFIGURACIÓN (Railway env vars)

### Wallet y RPC
```
WALLET_PUBKEY=F9kYAERneG7Qo9ZRrNBQ3pjfqiiv9FaTenMKwVTG9zaG    (tu wallet pública)
WALLET_PRIVKEY_B58=***                                           (tu private key — SECRETO)
SOLANA_RPC_HTTP=https://api.mainnet-beta.solana.com             (nodo HTTP)
SOLANA_RPC_WS=wss://api.mainnet-beta.solana.com                 (nodo WebSocket)
```

### Modo
```
LIVE_MODE=false                                                  (false=SIM, true=LIVE trading real)
```

### Dinero y riesgo
```
SIM_CAPITAL=50                    (capital inicial en simulación: $50)
MAX_TRADE_PCT=0.10                (máximo 10% del balance por trade)
MIN_TRADE_SOL=0.005               (mínimo 0.005 SOL = ~$0.75)
STOP_LOSS_PCT=0.70                (parar si pierde >30% del capital inicial)
MAX_SESSION_LOSS_PCT=0.20         (circuit breaker: parar si pierde >20% en sesión)
```

### Slippage y fees
```
SIM_SLIPPAGE_PCT=0.015            (1.5% de slippage por operación)
SIM_PRIORITY_FEE_SOL=0.0004       (0.0004 SOL en fees priority)
MAX_PRICE_IMPACT=2.0              (máximo 2% de price impact permitido)
```

### Wallets a copiar
```
TARGET_WALLETS=CyaE1Vx...,3LUfv2u...,Bi4rd5F...,(etc)    (11 wallets profesionales)
```

---

## 7. FLUJO DE EJECUCIÓN (Paso a paso)

### En SIMULACIÓN (LIVE_MODE=false)
```
1. Watcher detecta swap en blockchain
2. Executor.execute_copy() es llamado
3. No hay keypair → entra en simulación
4. Simulator.process() calcula P&L teórico
5. Se muestra resultado: "WIN +5%" o "LOSS -2%"
6. Balance se actualiza en memoria
7. Se guardan logs y reporte
```

### En LIVE (LIVE_MODE=true)
```
1. Watcher detecta swap en blockchain
2. Executor.execute_copy() es llamado
3. Se obtiene tu private key
4. Se calcula monto a invertir (proporcional)
5. Se piden quotes a Jupiter/PumpPortal
6. Se construye TX (transacción real)
7. Se firma con tu private key
8. Se envía a Solana blockchain
9. Se espera confirmación (15-20 segundos)
10. Se regresa la signature
11. Simulator.process() registra el trade REAL
```

---

## 8. ESTADO ACTUAL (6 mayo 2026)

### Problemas encontrados y resueltos
| Fecha | Problema | Solución |
|---|---|---|
| 26 abril | Latencia lenta (Helius 3s) | Agregado PumpPortal WS (0.5s) |
| 6 mayo | PumpPortal API falló (HTTP 400) | 3-backend fallback (PumpAPI + Jupiter) |
| 6 mayo | Pérdida de $10 en fees | 3 protecciones anti-fallos |
| 6 mayo | Circuit breaker no existía | Agregado (20% max loss) |
| 6 mayo | Bot copia trades 0-min | Cooldown de 2 min |

### Métrica de éxito (SIMULACIÓN)
```
Último run: 5 trades | 100% win rate | Balance $96.61 | ROI +93%
(pero estaba en $50 inicial, así que ganó $46.61 en poco tiempo)
```

### Próximos pasos
1. **Monitorear:** Capital debe estabilizarse con cooldown
2. **Verificar:** Win rate >60% consistente durante 1-2 semanas
3. **LIVE:** Cuando esté estable, activar con $200+ de capital
4. **Seguridad:** Activar 2FA en Railway + GitHub

---

## 9. COMMITS CLAVE

```
61a092d — Protecciones 1-3 (failed attempts, price impact, liquidez)
d769fe7 — Circuit breaker de sesión (MAX_SESSION_LOSS_PCT)
a1bceaa — Cooldown de 2 min (evita trades muy rápidos)
```

---

## 10. CÓMO SE VE EN LOGS

```
[21:39:38] [COPY BUY] [cyan]Theo[/] | ABC | SOL: 10.00 | Balance: $50.00
[21:39:45] [pumpfun] ✅ PumpPortal OK
[21:40:15] [SIM] ✅ WIN | Theo vendió ABC | +5.2% (+$2.60) | fee $0.036
[21:40:15] [SIM] 📊 RESUMEN | Trades: 1 | Win rate: 100% | Balance: $52.60
```

---

## 11. RESUMEN EN 3 PUNTOS

1. **Qué:** Bot que copia trades de 11 wallets profesionales en Solana
2. **Cómo:** Detecta transacciones → calcula % proporcional → ejecuta en tu wallet
3. **Seguridad:** 4 protecciones contra pérdidas + circuit breaker + validaciones en cada paso

---

## 12. PRÓXIMA META

**Activar LIVE mode con $200 de capital cuando:**
- ✅ Win rate >65% consistente (1-2 semanas SIM)
- ✅ Capital estable (no bajando)
- ✅ 2FA activado en Railway + GitHub
- ✅ Wallet solo contiene capital de trading (resto en wallet fría)

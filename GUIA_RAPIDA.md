# ⚡ Guía Rápida BotSolana

## En 30 segundos

**Bot = Copy trader automático**
- Monitoreas 11 wallets profesionales
- Cuando compran un token → tú compras el mismo %
- Cuando venden → tú vendes
- Objetivo: Copiar su 64% de ganancia

---

## Stack (todo lo que usa)

```
Python 3           → Lenguaje del bot
├── solders         → Construir transacciones
├── solana-py       → Conectar a blockchain
├── httpx           → Hacer requests HTTP
├── websockets      → Escuchar en vivo
└── rich            → Mostrar logs bonitos

APIs externas
├── Helius          → WebSocket para detectar swaps
├── PumpPortal      → Bonding curve (Pump.fun)
├── Jupiter         → Ruteo de swaps (DEX)
├── DexScreener     → Precios y liquidez
└── Solana RPC      → Nodo de blockchain

Servicios
├── Railway         → Servidor 24/7
├── GitHub          → Control de versión
└── Solana Mainnet  → Blockchain real

Variables de control (Railway env vars)
├── WALLET_PUBKEY   → Tu dirección pública
├── WALLET_PRIVKEY  → Tu clave privada (SECRETO)
├── LIVE_MODE       → false=SIM, true=LIVE
├── SIM_CAPITAL     → Dinero inicial
├── MAX_TRADE_PCT   → % máximo por trade
└── (+ 10 más)
```

---

## Cómo funciona en 5 pasos

```
1️⃣ DETECTOR (Watcher)
   Helius WS escucha blockchain
   → "Theo compró 5 SOL de token ABC"
   
2️⃣ DECISIÓN (Executor)
   ¿Theo invirtió 10% de su capital?
   → Tú inviertes 10% del tuyo ($50 si tienes $500)
   ✓ 4 validaciones de seguridad
   
3️⃣ EJECUCIÓN
   SIM MODE: calcula ganancia teórica
   LIVE MODE: envía transacción REAL a blockchain
   
4️⃣ CÁLCULO (Simulator)
   - Precio entrada: $1.00
   - Slippage entrada: 1.5%
   - Precio salida: $1.05
   - Slippage salida: 1.5%
   - Menos fees: -$0.12
   = GANANCIA: +1.66%
   
5️⃣ RESULTADO
   "✅ WIN +1.66% | Balance: $50.83"
   (en SIM)
```

---

## Las 4 Protecciones

```
┌────────────────────────────────────────┐
│ Protección 1: Failed Attempts          │
│ Si un token falla 2 veces → ignorarlo │
│ Ahorra: Evita gastar fees en lo mismo │
└────────────────────────────────────────┘

┌────────────────────────────────────────┐
│ Protección 2: Price Impact             │
│ Si impacto de precio >50% → abortar   │
│ Ahorra: No envía TX que fallarán      │
└────────────────────────────────────────┘

┌────────────────────────────────────────┐
│ Protección 3: Liquidez Mínima          │
│ Si liquidez <$500 → no comprar         │
│ Ahorra: Evita slippage extremo        │
└────────────────────────────────────────┘

┌────────────────────────────────────────┐
│ Protección 4: Cooldown 2 min           │
│ Si fue vendido hace <2 min → ignorar  │
│ Ahorra: No copia trades que ya perdieron
└────────────────────────────────────────┘
```

---

## Configuración actual en Railway

```
DINERO
├── SIM_CAPITAL = $50 (simulación inicial)
├── MAX_TRADE_PCT = 10% (máximo por trade)
└── STOP_LOSS_PCT = 70% (parar si pierde >30%)

SLIPPAGE & FEES
├── SIM_SLIPPAGE_PCT = 1.5% (realista Pump.fun)
├── SIM_PRIORITY_FEE_SOL = 0.0004 (en SOL)
└── MAX_PRICE_IMPACT = 2% (máximo permitido)

SEGURIDAD
├── MAX_SESSION_LOSS_PCT = 20% (circuit breaker)
└── LIVE_MODE = false (simulación, no dinero real)

WALLETS (11 profesionales)
├── Cented, Domy, Theo, Cupsey, Nyhrox
├── Cupsey-2, Decu, Orange, Insentos
├── Cupsey-Test, Trey
└── (monitorea TODOS simultáneamente)
```

---

## Cómo ver qué está pasando

### Comandos para monitorear

```bash
# Ver últimos 50 logs
railway logs --tail 50

# Ver solo ganadoras
railway logs --tail 200 | grep "WIN"

# Ver solo pérdidas
railway logs --tail 200 | grep "LOSS"

# Ver resumen
railway logs --tail 200 | grep "RESUMEN"

# Ver si hay errores
railway logs --tail 200 | grep "ERROR"

# Ver variables actuales
railway variables
```

### Qué significa cada log

```
✅ WIN +5.2% ($+2.60)
   → Trade ganador, ganó 5.2% = $2.60

❌ LOSS -3.0% ($-0.26)
   → Trade perdedor, perdió 3% = $0.26

⏰ AUTO-CLOSE (20+ min)
   → Bot cerró posición porque lleva abierta >20 min

💰 RESUMEN | Trades: 5 | Win rate: 80% | Balance: $54.00
   → 5 trades totales, 4 ganaron, 1 perdió, ganancia neta $4

🚨 CIRCUIT BREAKER ACTIVADO
   → Perdió >20% en sesión, TODOS los trades se detienen
```

---

## Estados del bot

```
SIMULACIÓN (actual)
  ✓ LIVE_MODE = false
  ✓ No usa dinero real
  ✓ Calcula teórico exacto
  ✓ Bueno para: validar estrategia
  
  Objetivo: Win rate >65% durante 1-2 semanas
            ↓
  
LIVE TRADING (meta)
  ✓ LIVE_MODE = true
  ✓ Dinero REAL en juego
  ✓ Transacciones reales en blockchain
  ⚠ Requiere: $200+ capital, 2FA, wallet fría
  
  Decisión: cuando esté listo
```

---

## Próximas acciones (plan)

```
HARCODED EN EL BOT ✓
├── 3 protecciones contra fallos (fees, price impact, liquidez)
├── Circuit breaker (para si pierde >20%)
├── Cooldown 2 min (evita trades muy rápidos)
└── Multi-backend fallback (PumpPortal → PumpAPI → Jupiter)

MONITOREO (2 semanas)
├── Capital debe estabilizarse (con cooldown)
├── Win rate debe ser >65% consistente
├── Sin circuit breaker disparándose
└── Logs sin errores (solo warnings normales)

ANTES DE LIVE (cuando esté listo)
├── 2FA en Railway account
├── 2FA en GitHub account
├── Depositar $200 en wallet trading (NO todo)
├── Ganancias → wallet fría (separate wallet)
└── Leer esta guía nuevamente

DURANTE LIVE
├── Monitorear logs 1-2 veces al día
├── Si loss >15%, revisar logs
├── Si circuit breaker dispara, evaluar causa
├── Mantener wallet separadas (trading / ganancias)
```

---

## Stack visual completo

```
┌─────────────────────────────────────────────────┐
│           TU WALLET EN SOLANA                    │
│    (F9kYAERneG7Qo9ZRrNBQ3pjfqiiv9FaTenMK...)    │
│                                                 │
│  Capital: $50-500 (según qué uses)              │
└─────────────────────────────────────────────────┘
          ↑ ↓ (envía TX, recibe SOL/tokens)
┌─────────────────────────────────────────────────┐
│        SOLANA BLOCKCHAIN (Mainnet)              │
│  - Inmutable                                    │
│  - 400ms block time                             │
│  - ~$0.0005 fee por transacción                 │
└─────────────────────────────────────────────────┘
     ↑ (11 wallets monitoreadas escuchan aquí)
┌─────────────────────────────────────────────────┐
│    HELIUS + PUMPPORTAL WEBSOCKET                │
│    (Escuchan transacciones en tiempo real)      │
│    Latencia: 0.5-3 segundos                     │
└─────────────────────────────────────────────────┘
          ↓
┌──────────────────────────────────────────────────────┐
│              BOT (Python 3 en Railway)               │
│                                                      │
│  WATCHER          EXECUTOR        SIMULATOR         │
│  ├─ Detecta    ├─ Valida (4x)  ├─ Calcula P&L     │
│  └─ Filtra     ├─ Construye TX │  ├─ Slippage     │
│                ├─ Firma        │  ├─ Fees         │
│                └─ Envía        │  └─ Balance      │
│                                                      │
│  PROTECCIONES: Failed attempts, Price impact,       │
│  Liquidez, Cooldown, Circuit breaker              │
│                                                      │
│  CONFIG: 60+ variables (config.py + Railway)        │
└──────────────────────────────────────────────────────┘
     ↓ (envía TX)        ↓ (pide quotes)
┌─────────────────────┐ ┌──────────────────────────┐
│  SOLANA MAINNET     │ │ JUPITER + DexScreener    │
│  (blockchain real)  │ │ (precios y liquidez)     │
└─────────────────────┘ └──────────────────────────┘
     ↓
┌─────────────────────────────────────────┐
│    RAILWAY LOGS                         │
│  (historial de todo lo que pasó)        │
│  ✅ WINS / ❌ LOSSES                     │
│  💰 BALANCE ACTUALIZADO                  │
└─────────────────────────────────────────┘
```

---

## Resumen de 1 minuto

| Qué | Dónde | Cuándo |
|---|---|---|
| **Bot copia** | 11 wallets profesionales | 24/7 automático |
| **Detecta** | Blockchain Solana (RPC + WS) | En tiempo real (0.5-3s) |
| **Valida** | 4 protecciones en executor.py | ANTES de cada trade |
| **Ejecuta** | SIM (teórico) o LIVE (real) | Depende LIVE_MODE |
| **Calcula** | Ganancias realistas | Después de vender |
| **Almacena** | Railway logs + JSON files | Persistente |
| **Monitorea** | Tú (leyendo logs) | 1-2 veces al día |

---

## Commit history

```
a1bceaa — Cooldown 2 min (evita trades rápidos)
d769fe7 — Circuit breaker 20% loss
61a092d — Protecciones 1-3 (failed, price impact, liquidez)
4722114 — Multi-backend fallback (PumpPortal + PumpAPI + Jupiter)
```

---

## Preguntas frecuentes

**¿Cuánto capital necesito?**
- SIM: cualquier cantidad ($1+)
- LIVE: mínimo $200 (volatilidad)

**¿Cuántas wallets copia?**
- 11 wallets profesionales simultáneamente

**¿Cuánto gana típicamente?**
- Simulación: 60-80% win rate
- LIVE: será similar (si protecciones funcionan)

**¿Qué pasa si pierde?**
- SIM: capital teórico baja (sin dinero real)
- LIVE: dinero real baja (pero limitado a -20% por circuit breaker)

**¿Se puede pausar?**
- SÍ: `railway variables set LIVE_MODE=false`
- El bot sigue detectando pero NO ejecuta

**¿Cuánto cuesta?**
- Railway: $12/mes (si usas bot + db)
- APIs: gratuitas (Helius, Jupiter, DexScreener)
- Fees Solana: ~$0.0005 por transacción real

---

## Próximo paso

**Monitorea por 2 semanas**, luego avisame cuando esté listo para LIVE.

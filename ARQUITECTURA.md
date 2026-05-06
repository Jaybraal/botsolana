# 🏗️ Arquitectura de BotSolana

## Flujo de datos global

```
┌─────────────────────────────────────────────────────────────────┐
│                    SOLANA BLOCKCHAIN                              │
│                  (Red descentralizada)                            │
└─────────────────────────────────────────────────────────────────┘
                            ↑ ↓
                  ┌─────────────────────┐
                  │  11 WALLETS COPIAR  │
                  │   (profesionales)   │
                  └─────────────────────┘
                            ↓
              ┌──────────────────────────────┐
              │  HELIUS + PUMPPORTAL         │
              │  WebSocket (detectan swaps)  │
              │  Latencia: 0.5-1s            │
              └──────────────────────────────┘
                            ↓
              ┌──────────────────────────────┐
              │     WATCHER.PY               │
              │  Filtra: Solo tus 11 wallets │
              │  Output: Diccionario swap    │
              └──────────────────────────────┘
                            ↓
              ┌──────────────────────────────┐
              │     EXECUTOR.PY              │
              │  Calcula monto proporcional  │
              │  - SIM: calcula P&L teórico  │
              │  - LIVE: envía TX real       │
              └──────────────────────────────┘
                   ↙              ↘
         ┌────────────────┐  ┌──────────────────┐
         │   SIMULATOR    │  │  TU WALLET REAL  │
         │  (teórico)     │  │  (LIVE MODE)     │
         │ P&L calcula    │  │ Transacción +    │
         │ Balance update │  │ Fees + Slippage  │
         └────────────────┘  └──────────────────┘
                ↓                      ↓
         ┌────────────────┐  ┌──────────────────┐
         │ LOGS + REPORTE │  │ BLOCKCHAIN       │
         │ (en Railway)   │  │ (confirmado)     │
         └────────────────┘  └──────────────────┘
```

---

## Componentes en detalle

### 1. WATCHER (Detector en tiempo real)

```
┌─────────────────────────────────────────────┐
│         WATCHER.PY                          │
│  Corre en paralelo con Executor             │
│                                             │
│  ┌────────────────────────────────────────┐ │
│  │ Helius WebSocket                       │ │
│  │ - Escucha TODOS los swaps en Solana    │ │
│  │ - Latencia: 1-3 segundos               │ │
│  │ - Datos: token_in, token_out, amount   │ │
│  └────────────────────────────────────────┘ │
│                   +                         │
│  ┌────────────────────────────────────────┐ │
│  │ PumpPortal WebSocket                   │ │
│  │ - Detecta bonding curve específicamente│ │
│  │ - Latencia: 0.5 segundos (más rápido) │ │
│  │ - Más preciso para tokens nuevos       │ │
│  └────────────────────────────────────────┘ │
│                   ↓                         │
│  ┌────────────────────────────────────────┐ │
│  │ Filter                                 │ │
│  │ if swap.wallet in TARGET_WALLETS:      │ │
│  │   → pasa a EXECUTOR                    │ │
│  │ else:                                  │ │
│  │   → ignora (no es de nuestras wallets) │ │
│  └────────────────────────────────────────┘ │
│                   ↓                         │
│  Output: swap_dict (JSON)                   │
│  {                                          │
│    "wallet": "Theo...",                     │
│    "token_in": "SOL",                       │
│    "token_out": "ABC123",                   │
│    "amount": 5000000000,  (en lamports)     │
│    "implied_price_sol": 0.00015,            │
│    ...                                      │
│  }                                          │
└─────────────────────────────────────────────┘
```

### 2. EXECUTOR (Ejecutor de trades)

```
┌──────────────────────────────────────────────────────────┐
│           EXECUTOR.PY (execute_copy)                      │
│                                                          │
│  Input: swap_dict (de WATCHER)                           │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │ CHEQUEOS DE SEGURIDAD (protecciones)               │  │
│  │                                                    │  │
│  │ 1. ¿Ya tenemos esta posición abierta?             │  │
│  │    → NO (ignorar, no abrir 2x)                    │  │
│  │                                                    │  │
│  │ 2. ¿Fue este token vendido hace <2 min?           │  │
│  │    (Protección cooldown - evita trades pérdida)  │  │
│  │    → SÍ (ignorar, está en cooldown)              │  │
│  │                                                    │  │
│  │ 3. ¿Este token ya falló 2 veces?                  │  │
│  │    (Protección failed attempts - ahorra fees)    │  │
│  │    → SÍ (ignorar)                                 │  │
│  │                                                    │  │
│  │ 4. ¿Tiene liquidez >= $500 en DexScreener?        │  │
│  │    (Protección liquidez mínima)                   │  │
│  │    → NO (ignorar, slippage sería extremo)        │  │
│  │                                                    │  │
│  │ 5. ¿Pasó el precio impact >50% a Jupiter?         │  │
│  │    (Protección price impact)                      │  │
│  │    → SÍ (abortar, TX fallaría)                   │  │
│  │                                                    │  │
│  │ 6. ¿Circuit breaker activado? (pérdida >20%)     │  │
│  │    → SÍ (parar todo)                              │  │
│  │                                                    │  │
│  └────────────────────────────────────────────────────┘  │
│                       ↓ (pasó todos)                     │
│  ┌────────────────────────────────────────────────────┐  │
│  │ CALCULAR MONTO (modo proporcional)                 │  │
│  │                                                    │  │
│  │ Theo: Invirtió 5 SOL en token (10% de su $500)    │  │
│  │ Tú:   Tienes $500 capital                         │  │
│  │ →     Inviertes 10% = $50 en el mismo token       │  │
│  │                                                    │  │
│  │ (no copy el monto absoluto, copy el %)            │  │
│  └────────────────────────────────────────────────────┘  │
│                       ↓                                   │
│  ¿Es SIMULACIÓN (LIVE_MODE=false)?                       │
│  ├─→ SÍ: va a SIMULATOR (teórico)                       │
│  └─→ NO: va a construir TX real                         │
│                                                          │
│       (LIVE BRANCH)                                       │
│       ┌─────────────────────────────────────┐           │
│       │ 1. Pedir quote a Jupiter (precio)   │           │
│       │ 2. Construir transacción            │           │
│       │ 3. Firmar con tu private key        │           │
│       │ 4. Enviar a Solana blockchain       │           │
│       │ 5. Esperar confirmación (15-20s)    │           │
│       │ 6. Retornar signature               │           │
│       │ 7. Pasar a SIMULATOR para registrar │           │
│       └─────────────────────────────────────┘           │
└──────────────────────────────────────────────────────────┘
```

### 3. SIMULATOR (Cálculo de P&L)

```
┌───────────────────────────────────────────────────┐
│          SIMULATOR.PY                             │
│  Calcula ganancias/pérdidas REALISTAS             │
│                                                  │
│  Input: swap_dict + monto a invertir              │
│                                                  │
│  ┌─────────────────────────────────────────────┐ │
│  │ COMPRA (is_buy)                             │ │
│  │                                             │ │
│  │ Precio entrada (DexScreener o implied):     │ │
│  │   $1.00                                     │ │
│  │                                             │ │
│  │ Ajuste slippage de entrada:                 │ │
│  │   $1.00 × (1 + 0.015) = $1.015 (peor)     │ │
│  │                                             │ │
│  │ ¿Por qué peor? En Solana compras peor que  │ │
│  │ el precio de mercado (bonding curve).       │ │
│  │                                             │ │
│  │ Monto en tokens:                            │ │
│  │   $50 / $1.015 = 49.26 tokens               │ │
│  │                                             │ │
│  │ Posición abierta ✓                          │ │
│  │   balance_antes = $100.00                   │ │
│  │   balance_después = $50.00 (invertido)      │ │
│  └─────────────────────────────────────────────┘ │
│                      ↓ (esperar venta)           │
│  ┌─────────────────────────────────────────────┐ │
│  │ VENTA (is_sell)                             │ │
│  │                                             │ │
│  │ Precio salida (DexScreener o implied):      │ │
│  │   $1.05 (subió +5%)                         │ │
│  │                                             │ │
│  │ Ajuste slippage de salida:                  │ │
│  │   $1.05 × (1 - 0.015) = $1.0342 (peor)    │ │
│  │                                             │ │
│  │ Valor de 49.26 tokens:                      │ │
│  │   49.26 × $1.0342 = $50.95                  │ │
│  │                                             │ │
│  │ Ganancia bruta: +$0.95 (+1.9%)              │ │
│  │                                             │ │
│  │ Menos FEES (priority fee round-trip):       │ │
│  │   0.0004 SOL × 2 × $150 = $0.12             │ │
│  │                                             │ │
│  │ GANANCIA NETA: +$0.95 - $0.12 = +$0.83     │ │
│  │ Porcentaje real: +1.66%                     │ │
│  │                                             │ │
│  │ Balance final:                               │ │
│  │   $50.00 + $0.83 = $50.83 ✓                 │ │
│  │                                             │ │
│  │ Resultado: ✅ WIN (+1.66%)                   │ │
│  │ Hold time: 3 min                            │ │
│  └─────────────────────────────────────────────┘ │
│                      ↓                           │
│  Registrar en historia de trades                 │
│  Actualizar balance total                        │
│  Mostrar en logs                                 │
└───────────────────────────────────────────────────┘
```

---

## 3 Rutas de Swap (cascada)

```
COMPRA DE TOKEN
       ↓
   ┌───────────────────────────┐
   │  1. PumpPortal (primario) │
   │  https://pumpportal.fun  │
   │  Bonding curve específico │
   └────────┬──────────────────┘
            │
      ¿Funcionó?
      ├─→ SÍ: Retorna TX ✓
      └─→ NO: intenta siguiente
            ↓
   ┌───────────────────────────┐
   │ 2. PumpAPI.fun (alternativa
   │    https://pumpapi.fun    │
   │    Misma especificidad     │
   └────────┬──────────────────┘
            │
      ¿Funcionó?
      ├─→ SÍ: Retorna TX ✓
      └─→ NO: intenta siguiente
            ↓
   ┌───────────────────────────┐
   │ 3. Jupiter on-chain        │
   │    v6 API + RPC            │
   │    Fallback robusto        │
   └────────┬──────────────────┘
            │
      ¿Funcionó?
      ├─→ SÍ: Retorna TX ✓
      └─→ NO: ERROR (trade cancelado)
```

---

## Variables de Control (Railway env vars)

```
WALLET & BLOCKCHAIN
├── WALLET_PUBKEY          (tu dirección pública)
├── WALLET_PRIVKEY_B58     (tu clave privada - SECRETO)
├── SOLANA_RPC_HTTP        (nodo HTTP)
└── SOLANA_RPC_WS          (nodo WebSocket)

DINERO & RIESGO
├── LIVE_MODE              (false=SIM, true=LIVE)
├── SIM_CAPITAL            (capital inicial)
├── MAX_TRADE_PCT          (% máximo por trade)
├── STOP_LOSS_PCT          (parar si pierde X%)
└── MAX_SESSION_LOSS_PCT   (circuit breaker)

SLIPPAGE & FEES
├── SIM_SLIPPAGE_PCT       (1.5% por operación)
├── SIM_PRIORITY_FEE_SOL   (fee en SOL)
└── MAX_PRICE_IMPACT       (máximo 2%)

WALLETS A COPIAR
└── TARGET_WALLETS         (11 wallets profesionales)
```

---

## Estados posibles del bot

```
┌─────────────────────────────────────────┐
│   SIMULACIÓN (LIVE_MODE=false)         │
│                                         │
│ ✓ No usa tu wallet real                 │
│ ✓ Calcula P&L teórico exacto            │
│ ✓ 100% seguro (no hay dinero real)      │
│ ✓ Bueno para: validar estrategia       │
│                                         │
│ Esperar: 1-2 semanas con >65% win rate │
│ Siguiente: LIVE MODE                   │
└─────────────────────────────────────────┘
             ↓
┌─────────────────────────────────────────┐
│   LIVE MODE (LIVE_MODE=true)            │
│                                         │
│ ✓ Trades REALES en blockchain          │
│ ✓ Dinero real en juego                 │
│ ✓ Fees reales deducidos                │
│ ⚠ Riesgo: dinero puede perderse        │
│                                         │
│ Requisitos:                             │
│ - Win rate >65% en SIM                  │
│ - Capital >= $200 (no menos)            │
│ - 2FA en Railway + GitHub               │
│ - Wallet fría para ganancias            │
└─────────────────────────────────────────┘
             ↓
┌─────────────────────────────────────────┐
│   CIRCUIT BREAKER ACTIVADO              │
│   (pérdida >20% en sesión)              │
│                                         │
│ - TODOS los trades se detienen          │
│ - Tienes que reiniciar el bot           │
│ - Evaluar qué salió mal                 │
│ - Reactivar cuando esté listo           │
└─────────────────────────────────────────┘
```

---

## Monitoreo (cómo saber si funciona)

```
LOGS NORMALES (todo bien)
├── [COPY BUY] [cyan]Theo[/] | ABC | $50.00
├── [pumpfun] ✅ PumpPortal OK
├── [SIM] ✅ WIN | Theo vendió ABC | +5.2%
└── [SIM] 📊 RESUMEN | Win rate: 75% | Balance: $525.00

ADVERTENCIAS (revisar pero OK)
├── [SIM] Liquidez $200 < $500 — abortando
├── [SIM] Price impact 45% < 50% — abortando
└── [SIM] ya falló 2 veces — ignorando

ERRORES (investigar)
├── [ERROR] No se pudo obtener balance SOL
├── ❌ [SIM] LOSS | -25.0% (-$12.50)
└── 🚨 CIRCUIT BREAKER ACTIVADO — Pérdida: 22%
```

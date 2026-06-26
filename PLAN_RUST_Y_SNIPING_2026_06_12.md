# BotSolana — Análisis completo, plan de migración a Rust y entrenamiento de IA para sniping
**Fecha:** 2026-06-12
**Estado:** DOCUMENTO DE PLANIFICACIÓN — no se ha cambiado código todavía.
**Ejecución de la migración:** prevista con Claude Haiku, usando este documento como guía.

---

## 0. Resumen ejecutivo (TL;DR)

1. **El sistema está bien construido para lo que es:** un copy-trader async en Python con scanner autónomo y un scorer estadístico. ~6.400 líneas, arquitectura producer-consumer limpia, ya optimizada en latencia (commits recientes de async + hot path).
2. **Hallazgo crítico del análisis de datos:** las wallets que copias (Cupsey, Theo, Nyhrox, Decu) **son snipers**. Ganan entrando a tokens de **1-3 min de edad** con **88-90% WR** y retornos medios de miles de %. Cuando las **copias**, entras 1-3 s después → cazas la cola del movimiento. **Tu instinto de pasar a sniping en vez de copy es correcto: conviene volverte el sniper, no el copiador tardío.**
3. **Sobre Rust:** para *copy trading* Rust no aportaba (no es juego de velocidad). Para *sniping* **sí** importa la latencia — pero el 90% de la ventaja de un sniper viene de **infraestructura** (Jito bundles, geyser/RPC pagado, priority fee) y de **selección de token (la IA)**, no del lenguaje. **Recomendación: NO reescritura total. Híbrido — un microservicio Rust SOLO para el hot path (detectar→firmar→enviar), Python sigue siendo el cerebro (estrategia + IA + reportes).**
4. **Entrenar la IA para sniping ya es posible con los datos actuales** (4.913 trades, 2.487 con outcome). Hay señal data-driven clara por edad/momentum/mcap.

---

## 1. MAPA COMPLETO DEL SISTEMA

### 1.1 Stack
- **Lenguaje:** Python 3 (async/await, asyncio)
- **Conectividad:** `websockets`, `httpx` (async), Helius RPC WS, PumpPortal WS
- **DEX/quotes:** Jupiter v6 API, PumpPortal API (bonding curve), DexScreener
- **Firma on-chain:** `solana` + `solders` (solders es Rust por debajo → la firma YA es rápida)
- **TUI/reportes:** `rich`, `reportlab` (PDF)
- **Deploy:** Railway (servicio `botsolana`), GitHub `Jaybrael/botsolana`
- **Datos:** SQLite (`data/wallet_history.db`), JSONL/JSON en `data/`

### 1.2 Diagrama de flujo (alto nivel)

```
                    ┌─────────────────────────────────────────┐
                    │              main.py                      │
                    │   asyncio.run(watch_all())                │
                    └───────────────────┬──────────────────────┘
                                        │
        ┌───────────────────────────────┼────────────────────────────────┐
        │                               │                                 │
  ┌─────▼─────┐                  ┌──────▼───────┐                ┌────────▼────────┐
  │ watch()   │  Helius WS       │watch_pumpportal│ PumpPortal WS │watch_autonomous │ (si AUTONOMOUS_MODE)
  │ (logsSub) │  todos los DEX   │ Pump.fun BC    │  ~0.5s        │  scanner snipe  │
  └─────┬─────┘                  └──────┬───────┘                └────────┬────────┘
        │ detect_swap (decoder)         │ _pumpportal_to_swap            │ subscribeNewToken
        │                               │                                │ + subscribeTokenTrade
        └──────────────┬────────────────┘                                │ stat_scorer.score_token
                       │ put_nowait(swap)                                 │ execute_copy (buy/sell)
                       ▼                                                  │
              ┌─────────────────┐  asyncio.Queue(maxsize=200)            │
              │  _swap_queue     │◄───────────────────────────────────────┘
              └────────┬────────┘
                       │  N consumers paralelos (SWAP_CONSUMERS=3)
              ┌────────▼─────────┐
              │ _swap_consumer   │ → execute_copy(swap)
              └────────┬─────────┘
                       ▼
        ┌──────────────────────────────────────────────┐
        │ execute_copy (copytrade/executor.py)          │
        │  ├─ SIM  → _simulate() → simulator.py         │
        │  └─ LIVE → Jupiter (AMM) / PumpPortal (BC)    │
        │      ├─ FAST_COPY: skip DexScreener+scorer    │
        │      ├─ scorer.should_copy (Groq patterns)    │
        │      ├─ protecciones (liq, price impact, dead)│
        │      └─ circuit breaker + scaling tiers       │
        └──────────────────────────────────────────────┘

  Background loops: _refresh_blockhash_loop, _refresh_balance_loop (sacan RPC del hot path)
  ETH paralelo: eth_watcher / alchemy_webhooks (rama Ethereum, separada)
```

### 1.3 Módulos (qué hace cada uno)

| Archivo | Líneas | Rol |
|---|---|---|
| `main.py` | 247 | Entry point, banner, paneles rich, arranca `watch_all()` |
| `config.py` | ~175 | TODO el config: wallets, labels, weights, risk tiers, scaling, programas Solana |
| `copytrade/watcher.py` | 469 | **Hot path detección.** Helius WS + PumpPortal WS + cola + consumers paralelos |
| `copytrade/executor.py` | 951 | **Hot path ejecución.** Compra/venta real (Jupiter/PumpPortal) + SIM + protecciones |
| `copytrade/autonomous_scanner.py` | 565 | **Lo más cercano a sniping hoy.** Detecta tokens nuevos en Pump.fun, trackea, scorea, compra, monitorea SL/TP |
| `copytrade/simulator.py` | 807 | Simulador "realismo brutal": fees, slippage dinámico, market impact, fail rate |
| `copytrade/scorer.py` | 170 | "IA" #1 — patrones Groq por wallet (HOY VACÍO: `groq_patterns.json` = `{}`) |
| `copytrade/stat_scorer.py` | 142 | "IA" #2 — scorer estadístico determinista (umbrales HARDCODEADOS). Lo usa el scanner autónomo |
| `copytrade/decoder.py` | 152 | Parser de transacciones Solana → detecta swaps |
| `copytrade/learner.py` | 286 | Analiza ganadores vs perdedores, genera `learner_rules.json` |
| `data_collector/fetch_history.py` | 426 | Descarga historial on-chain de las wallets → SQLite |
| `data_collector/compute_outcomes.py` | 254 | Empareja compra↔venta, calcula `pnl_pct` y `outcome` WIN/LOSS |
| `data_collector/groq_analyzer.py` | 289 | **Pipeline de entrenamiento IA:** Groq analiza DB → `groq_patterns.json` |
| `utils/*` | ~1.000 | jupiter, pumpfun, dexscreener, alchemy, blockchain, logger, market_context, scoring |
| Rama ETH | ~600 | `eth_watcher`, `eth_executor`, `eth_simulator`, `alchemy_webhooks` (secundaria) |

### 1.4 Estado actual (verificado hoy)
- `LIVE_MODE`: en `.env` local `WALLET_PUBKEY` vacío → **SIMULACIÓN**. (en Railway puede diferir)
- `AUTONOMOUS_MODE`: activable por env. El scanner autónomo existe y funciona.
- `groq_patterns.json` = `{}` → **el scorer Groq está efectivamente DESACTIVADO** (deja pasar todo con score 50). El que decide de verdad en autónomo es `stat_scorer.py` (hardcodeado).
- DB de entrenamiento: **4.913 trades, 2.487 con outcome** (1.984 WIN / 503 LOSS).

---

## 2. ANÁLISIS DE DATOS — la base para la decisión

### 2.1 WR por edad del token al comprar (de los 2.487 trades con outcome)

| Edad al comprar | n | WR (pnl>0) | WR realista (pnl>30%) | % que hizo 2x+ |
|---|---|---|---|---|
| < 1 min (snipe puro) | 829 | 79.1% | 70.7% | 63.0% |
| **1-3 min** | **287** | **90.6%** | **88.5%** | **86.4%** |
| 3-10 min | 157 | 87.3% | 80.9% | 78.3% |
| 10-30 min | 158 | 81.6% | 80.4% | 76.6% |
| 30 min+ | 147 | 78.9% | 72.8% | 67.3% |

**Lectura:** la zona de oro es **1-3 minutos de edad**. <1 min es bueno pero más ruidoso (riesgo de rug instantáneo). Después de 30 min el edge cae.

### 2.2 WR por momentum (buys en ventana corta)

| buys_5m | n | WR |
|---|---|---|
| <10 | 1869 | 81.6% |
| 10-50 | 33 | 81.8% |
| 50-150 | 21 | 90.5% |
| 300+ | 32 | 87.5% |

### 2.3 WR por market cap al entrar (bonding curve)

| mcap | n | WR | avg hold |
|---|---|---|---|
| <10k | 1746 | 81.6% | 9.5 min |
| 10-30k | 83 | 81.9% | 26 min |
| **30-70k** | 46 | **91.3%** | 48 min |
| 70-150k | 32 | 87.5% | 14 min |
| 150k+ | 55 | 74.5% | 182 min |

### 2.4 ⚠️ Notas críticas sobre la calidad de los datos
- **El WR (79-90%) NO es alcanzable copiando.** Es el WR *de las wallets élite cuando ELLAS snipean*. Mide su habilidad, no la nuestra. Cuando copiamos con 1-3 s de retraso entramos a un precio peor → nuestro WR real es mucho menor (memorias previas: ~50-55%).
- `outcome = "WIN" si pnl_pct > 0` — **no descuenta fees**. Por eso la columna "WR realista (pnl>30%)" es más honesta.
- `avg_pnl` está inflado por outliers (max +615.806%): son tokens donde la wallet entró casi a cero en la bonding curve. Confirma el punto: **ganan POR snipear temprano**.
- **Conclusión estratégica:** el valor no está en copiar a los snipers tarde, sino en **snipear nosotros mismos** con buena selección de token. De ahí que entrenar la IA para sniping sea la jugada correcta.

---

## 3. ¿RUST SÍ O NO? — análisis honesto

### 3.1 Dónde Python NO es el cuello de botella (Rust no ayuda)
- **Firma de transacciones:** `solders` ya es Rust por debajo. Firmar es microsegundos.
- **Construcción de tx:** idem.
- **Round-trip de red (RPC/WS):** lo domina la red e infraestructura, no el lenguaje. 50-300 ms que Rust no reduce.
- **Landing de la tx:** depende de priority fee + Jito tip + a qué leader le llega. Cero relación con el lenguaje.

### 3.2 Dónde Rust SÍ ayuda para sniping
- **Latencia del hot loop determinista:** sin GIL, sin overhead de asyncio, sin pausas de GC. En sniping competido, evitar jitter de 5-30 ms importa.
- **Geyser/Yellowstone gRPC:** streaming de cuentas/slots nativo y muy eficiente (Python puede, pero Rust es el ciudadano de primera clase).
- **Envío paralelo agresivo:** mandar la misma tx a N RPCs + Jito simultáneamente con control fino.
- **Co-location:** si algún día rentas un server cerca de los validadores, Rust exprime los microsegundos.

### 3.3 Veredicto
> **Las mayores mejoras para sniping NO requieren Rust:** (1) Jito bundles + tip, (2) RPC/geyser pagado (Helius/Triton), (3) priority fee dinámico, (4) buena IA de selección. Todo eso es Python. **Rust solo vale la pena cuando esas 4 ya estén hechas y la latencia sea, demostrablemente, el límite.**

**Recomendación: arquitectura híbrida, no reescritura total.**
- **Python (se queda):** estrategia, IA/scorer, simulador, reportes, orquestación, rama ETH, config.
- **Rust (nuevo, opcional, solo si hace falta):** un microservicio `sniper-core` que haga SOLO detectar→decidir-rápido→firmar→enviar para Pump.fun. Python le pasa la "lista blanca" de criterios y recibe los fills.

⚠️ **Nota para hacerlo con Haiku:** una reescritura total a Rust de 6.400 líneas es de **alto riesgo** para un modelo pequeño (manejo de errores async, firma, edge cases de PumpPortal). Un **componente Rust acotado** (~800-1.500 líneas, una sola responsabilidad) es mucho más apropiado y testeable. **Si se va a usar Haiku, hacer el híbrido acotado, no el rewrite.**

---

## 4. PLAN DE MIGRACIÓN A RUST (híbrido recomendado)

> Objetivo: microservicio `sniper-core` en Rust que snipea Pump.fun con mínima latencia, controlado por la IA de Python. Cada fase es independiente, testeable y reversible.

### Pre-requisitos (hacer ANTES de escribir Rust — son los que de verdad mueven la aguja)
1. **RPC/geyser pagado:** confirmar plan Helius (o Triton) con acceso a Geyser/Yellowstone gRPC y staked connection para envío de tx.
2. **Jito:** decidir si se usa Jito Block Engine (bundles + tip). Es lo que más sube el landing rate en sniping.
3. **Capital mínimo:** mismo criterio que live mode — **mínimo $200**, nunca $60 (fees/volatilidad).
4. **Wallet dedicada** para el sniper (no la principal), con SOL para tips/fees.

### Fase 0 — Decisiones de arquitectura (documentar, sin código)
- Lenguaje de interproceso Python↔Rust: **opción A** (recomendada) IPC simple por **stdin/stdout JSON lines** o un **socket local**; **opción B** WebSocket local; **opción C** compilar Rust como módulo Python con `pyo3/maturin` (más acoplado, más potente, más difícil para Haiku → evitar al principio).
- Crates Rust base: `solana-client`, `solana-sdk`, `solders`/`spl-token`, `yellowstone-grpc-client` (geyser), `jito-sdk` o llamadas HTTP a Jito, `tokio` (async), `reqwest`, `serde`/`serde_json`, `anyhow`.

### Fase 1 — Esqueleto del `sniper-core` (Rust)
- `cargo new sniper-core`, estructura: `src/main.rs`, `config.rs`, `geyser.rs`, `strategy.rs`, `executor.rs`, `jito.rs`.
- Cargar config desde env (mismas variables que Python: `AUTO_*`, `SNIPE_*`).
- Logging con `tracing`.
- **Test de fase:** compila y arranca, lee config, imprime "listo".

### Fase 2 — Ingesta de tokens nuevos (Rust)
- Conectar a PumpPortal WS (`subscribeNewToken`) o, mejor, a Geyser gRPC filtrando el programa Pump.fun BC (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`).
- Parsear evento de creación: mint, vSol/vTok (precio), mcap, creador.
- Acumular trades por token (buys/sells) igual que `autonomous_scanner._handle_token_trade`.
- **Test de fase:** loguea cada token nuevo con su precio derivado de la bonding curve. Comparar 10 min contra el log de Python para validar paridad.

### Fase 3 — Estrategia de snipe (Rust, espejo del snipe_scorer Python)
- Implementar el scorer data-driven (ver §5) leyendo `snipe_patterns.json` generado por Python.
- Reglas: edad ideal 1-3 min, momentum (buys/seg), mcap 10-70k, ratio buy/sell, anti-rug (dev no vendió, no honeypot).
- Devolver `decision: BUY/SKIP + score + razón`.
- **Test de fase:** correr en modo "dry-run" (solo decide, no compra) en paralelo al Python y comparar decisiones.

### Fase 4 — Ejecución (Rust)
- Construir tx de compra a Pump.fun BC (o vía Jupiter si ya migró a AMM).
- Blockhash cacheado y refrescado en background (igual que `_refresh_blockhash_loop`).
- Firmar con la keypair dedicada.
- Enviar: priority fee dinámico + (opcional) bundle Jito con tip.
- Confirmar / reintentar con backoff.
- **Test de fase:** primero en devnet o con montos mínimos ($1-2). Verificar fills reales y medir latencia detección→fill vs Python.

### Fase 5 — Gestión de posición y salida (Rust o delegada a Python)
- Monitor de precio (bonding curve) cada N s: SL / TP / trailing / timeout (mismos parámetros que `autonomous_scanner`).
- **Opción simple:** que Rust solo COMPRE rápido y notifique a Python; Python gestiona la salida (menos código crítico en Rust).
- **Test de fase:** ciclo completo compra→venta con métricas.

### Fase 6 — Puente Python↔Rust
- Python `autonomous_scanner` deja de comprar directo y, en su lugar, lanza/gestiona el proceso `sniper-core`, le pasa la config/criterios y consume sus fills para loguear, simular contabilidad y reportes.
- Mantener un flag `SNIPER_ENGINE=python|rust` para poder volver atrás al instante.
- **Test de fase:** A/B — un día Python, un día Rust, comparar latencia y WR.

### Fase 7 — Hardening
- Reconexión WS/gRPC con jitter (ya existe el patrón en Python, replicar).
- Circuit breaker (pérdida máxima de sesión) — **NO omitir, es seguridad de capital**.
- Manejo de "token muerto" / dedupe de mints.
- Persistencia de posiciones (Railway Volume — pendiente histórico del proyecto).

### Estimación honesta de esfuerzo
- Híbrido acotado (fases 1-6): **2-4 semanas** de trabajo enfocado para alguien con Rust; más con Haiku iterando.
- Rewrite total: **2-4 meses** y se pierde el ecosistema Python (IA, reportes). **No recomendado.**

---

## 5. PLAN: ENTRENAR LA IA PARA SNIPING

> Estado actual: hay DOS scorers. `scorer.py` (Groq, hoy vacío) y `stat_scorer.py` (hardcodeado). Para sniping conviene un **tercer scorer data-driven y específico de launch-time**, porque en sniping NO tenemos `price_change_1h/5m` (el token acaba de nacer) — solo edad, momentum (buys/seg), mcap y reservas de la bonding curve.

### 5.1 Por qué un scorer nuevo y no reusar el actual
- `stat_scorer` usa features que en sniping no existen aún (1h change) y umbrales hardcodeados de un análisis viejo.
- El scorer de sniping debe entrenarse SOLO con trades de edad baja y SOLO con features disponibles en el primer minuto.

### 5.2 Features disponibles en el momento del snipe (tiempo real)
- `token_age_min` (segundos desde creación)
- `buys` y `sells` acumulados del WS → **velocidad de buys** (buys/seg) y **ratio buy/sell**
- `v_sol`, `v_tok` → precio, y `mcap` derivado de la bonding curve
- `liq` ≈ SOL en la curva
- Señales anti-rug: ¿el creador vendió ya?, ¿concentración de holders?, ¿% supply en bonding curve?

### 5.3 Pipeline de entrenamiento propuesto (todo Python, reutiliza lo existente)
1. **Ampliar `fetch_history.py`** para capturar, por cada compra de las wallets élite, también: velocidad de buys en el primer minuto y si el creador vendió (si la API lo permite). Si no, trabajar con lo que ya hay en la DB.
2. **Nuevo `data_collector/snipe_trainer.py`:** lee `wallet_history.db`, filtra a `token_age_min < 5`, y calcula WR + avg_pnl por bucket de (edad, momentum, mcap). Emite `data/snipe_patterns.json` **derivado de datos, no hardcodeado**. (La §2 de este doc ya tiene los números base.)
3. **Nuevo `copytrade/snipe_scorer.py`:** carga `snipe_patterns.json` y puntúa un token en vivo. Mismo contrato que `stat_scorer.score_token(token_info) -> (score, passed, reason)`.
4. **Conectar al scanner autónomo:** flag `SNIPE_MODE=true` para que `autonomous_scanner` use `snipe_scorer` en vez de `stat_scorer`, con `AUTO_EVAL_DELAY_MIN` bajo (entrar en la ventana 1-3 min, no esperar 7).
5. **(Opcional) Capa LLM Groq:** re-entrenar `groq_patterns.json` con `groq_analyzer.py` PERO con un prompt orientado a sniping (qué predice un winner en el primer minuto). Sirve de "segunda opinión", no de decisor único.
6. **Validar en SIM** una semana con el simulador realista antes de cualquier cosa en vivo.

### 5.4 Parámetros de sniping sugeridos (data-driven, de la §2)
```
SNIPE_MIN_AGE_SEC=30        # evitar el ruido de los primeros 30s (rugs instantáneos)
SNIPE_MAX_AGE_MIN=3         # zona de oro 1-3 min (88.5% WR realista en histórico élite)
SNIPE_MIN_BUYS_PER_SEC=...  # derivar de datos (momentum)
SNIPE_MCAP_MIN=10000        # 10k
SNIPE_MCAP_MAX=70000        # 70k (zona de WR 91%)
SNIPE_MIN_BUY_SELL_RATIO=2  # más compras que ventas
SNIPE_TAKE_PROFIT_PCT=...   # los winners hacían 2x+; TP alto + trailing
SNIPE_STOP_LOSS_PCT=-25
SNIPE_MAX_HOLD_MIN=6
```
⚠️ **Recordatorio honesto:** el 88-90% WR del histórico es de las wallets élite. Nuestro sniper real tendrá WR menor (competimos contra ellas y contra otros bots). El objetivo realista inicial es **WR neto > 55% con profit factor > 1.3 en SIM** antes de ir en vivo.

---

## 6. RIESGOS Y NOTAS GENERALES

- **Railway filesystem efímero:** sigue pendiente el **Railway Volume**. Sin él, `data/` (balance, posiciones, patrones) se borra en cada redeploy. **Esto es bloqueante para evaluar rendimiento real** — resolverlo ANTES de Rust o sniping en vivo.
- **Seguridad de capital:** nunca quitar el circuit breaker ni el `MIN_RESERVE_SOL`. Mínimo $200 para live. Wallet dedicada para el sniper.
- **PumpPortal fragilidad:** ya falló con HTTP 400 (6 mayo). El sniper Rust debería poder usar Geyser directo como alternativa.
- **Competencia:** sniping de Pump.fun es un campo muy competido (bots, MEV). Sin Jito + RPC pagado, el landing rate será bajo aunque la IA acierte.
- **No borrar variables de Railway sin confirmación** (regla histórica del proyecto).
- **`groq_patterns.json` vacío:** decidir si re-entrenarlo o jubilar el scorer Groq en favor del snipe_scorer data-driven.

---

## 7. ORDEN RECOMENDADO DE EJECUCIÓN

1. **Railway Volume** (persistencia) — desbloquea todo lo demás.
2. **Entrenar la IA de sniping en Python** (§5: snipe_trainer + snipe_scorer) — barato, rápido, validable en SIM.
3. **Infraestructura de sniping** (RPC/geyser pagado + Jito) — el verdadero acelerador.
4. **SOLO si la latencia sigue siendo el límite tras 1-3:** microservicio Rust `sniper-core` (§4, híbrido acotado).
5. Reescritura total a Rust: **no**, salvo que el proyecto ya genere dinero y lo justifique.
```
```
```

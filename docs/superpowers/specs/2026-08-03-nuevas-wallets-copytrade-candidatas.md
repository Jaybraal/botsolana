# Diseño: Wallets candidatas nuevas para copytrade

**Fecha:** 2026-08-03
**Estado:** Aplicado en local (SIM) — `TARGET_WALLETS` (`.env`) y `WALLET_LABELS` (`config.py`) actualizados con las 8 candidatas. `LIVE_MODE` sigue en `false`, `ELITE_WALLETS`/`WALLET_WEIGHTS` sin tocar hasta que se gradúen. **Railway (producción) no se tocó** — este cambio solo existe en el `.env` local.
**Objetivo:** Ampliar `TARGET_WALLETS` con candidatas verificadas fuera del set actual de 7 wallets, sin tocar el modo LIVE hasta que pasen el mismo criterio de graduación que ya usa el bot.

---

## Contexto y motivación

`TARGET_WALLETS` actual (7 wallets, `LIVE_MODE=false`, `SNIPE_MODE=true`):

| Wallet | Address | Estado en config |
|---|---|---|
| Theo | `Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt` | ELITE (89.3% WR) |
| Nyhrox | `6S8GezkxYUfZy9JPtYnanbcZTMB87Wjt1qx3c6ELajKC` | ELITE (87.1% WR) |
| Cupsey ⭐ | `2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f` | ELITE (84.9% WR) + weight 10% |
| Decu | `4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9` | ELITE (84.4% WR) + weight 30% |
| Cupsey-2 | `4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk` | weight 40% (61.5% WR) |
| Cented | `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o` | weight 20% (44.4% WR) |
| Domy | `3LUfv2u5yzsDtUzPdsSJ7ygPBuqwfycMkjpNreRR2Yww` | sin peso asignado |

Se investigaron traders de memecoins conocidos en foros/X (Cupsey, Kimchi y otros) más un barrido de wallets "ballena" en Solana, con el objetivo de encontrar candidatas nuevas para copiar. Metodología aplicada:

1. Lista cruda de 203 wallets etiquetadas ("whale wallet list") sacada de un sitio de la comunidad (thealphaclub.io) — crowdsourced, sin garantía de calidad.
2. Verificación real on-chain vía Solana RPC (`getSignaturesForAddress`) sobre las 203 → **118 (58%) nunca tuvieron ni una transacción**, son basura/placeholder. 4 direcciones estaban malformadas. Varias estaban mal etiquetadas como "trader" siendo en realidad wallets de exchange (Binance, Bitget, MEXC) o de protocolo (Orca).
3. Cruce contra el leaderboard en vivo de `kolscan.io` (rankea por PNL real del día, no por reputación) para quedarnos solo con wallets que **hoy** siguen operando con PNL positivo.

**Hallazgo clave:** el propio `config.py` ya tiene a Cupsey, Decu, Theo y Cented correctos y coincidiendo con el leaderboard en vivo — confirma que la selección actual está bien fundamentada. Kimchi (trader viral por su claim de $40M) no tiene wallet verificable on-chain — su claim es cuestionado y no hay evidencia on-chain que lo respalde, así que **no se incluye como candidata**.

---

## Candidatas nuevas (no están en `TARGET_WALLETS` hoy)

Todas verificadas: (a) actividad on-chain reciente confirmada vía RPC, (b) aparecen HOY en el leaderboard de `kolscan.io` con PNL positivo.

| Nombre | Address | Rank kolscan hoy | PNL SOL (hoy) |
|---|---|---|---|
| The Doc | `DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt` | #1 | +128.83 |
| Nach | `9jyqFiLnruggwNn4EQwBNFXwpbLM9hrA4hV59ytyAVVz` | #5 | +82.81 |
| Casino | `8rvAsDKeAcEjEkiZMug9k8v1y8mW6gQQiMobd89Uy7qR` | #6 | +74.65 |
| Latuche | `GJA1HEbxGnqBhBifH9uQauzXSB53to5rhDrzmKxhSU65` | #11 | +55.17 |
| Yenni | `5B52w1ZW9tuwUduueP5J7HXz5AcGfruGoX6YoAudvyxG` | #16 | +41.06 |
| MACXBT | `ETU3GyrUsv6UztQJxHgsBX2UoJFmq79WJe3JyDpAqGMz` | #23 | +33.19 |
| Daumen | `8MaVa9kdt3NW4Q5HyNAm1X5LbR8PQRVDc1W8NMVK88D5` | #44 | +13.56 |
| Tom | `CEUA7zVoDRqRYoeHTP58UHU6TR8yvtVbeLrX1dppqoXJ` | #45 | +13.31 |

Nota: el rank/PNL de kolscan es de **un solo día** (2026-08-03) — no es win rate histórico como las wallets actuales del bot (que sí tienen WR calculado sobre trades reales copiados). No confundir "top hoy" con "élite comprobada".

### Lista secundaria (Tier 2, ~55 wallets)
Activas on-chain en los últimos 7 días pero sin cruce con el leaderboard de hoy — probablemente reales, pero no verificadas por PNL. Guardadas en `/private/tmp/claude-501/-Users-branel/f35d778e-b6d6-4827-826e-7070d30f9fa6/scratchpad/wallets_filtradas_final.json` (temporal, se pierde al cerrar la sesión de esa conversación). No se recomienda copiarlas directo — quedan como pool de exploración futura si `learner_scanner` necesita más wallets de referencia para patrones.

---

## Riesgos específicos de esta fuente de datos

1. **Rotación de wallets:** Cupsey ya cambió de wallet una vez tras perder $500K en un drenaje (`suqh5sHtr8HyJ7q8scBimULPkPpA557prMG47xCHQfK` → `2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f`, ya usada en config.py). Cualquier candidata nueva puede rotar igual sin aviso — el bot debe seguir usando `WALLET_LABELS` + logs para detectar si una wallet deja de operar de golpe.
2. **Wallets falsas / impersonación:** el espacio de memecoins tiene wallets que se hacen pasar por traders conocidos para atraer copytraders. El cruce con kolscan.io reduce el riesgo pero no lo elimina — kolscan también es una fuente de terceros, no verificación oficial de identidad.
3. **PNL de un solo día no es win rate:** las candidatas de la tabla están "calientes" hoy, no tienen el historial de cientos de trades que sí tienen las wallets actuales del bot.

---

## Plan de graduación (mismo criterio que ya usa el proyecto, ver `2026-06-30-autonomous-learner-scanner-design.md`)

1. **No tocar `LIVE_MODE` ni `TARGET_WALLETS` en Railway todavía.**
2. Añadir las 8 candidatas a `TARGET_WALLETS` en local/SIM (`LIVE_MODE=false`), correr en paralelo a las 7 actuales.
3. Dejar correr ≥ 2 semanas o hasta acumular una muestra mínima de trades por wallet (mismo umbral que ya usa el proyecto: suficiente para que `WR` sea estadísticamente significativo, no un solo trade suerte).
4. Promover a `ELITE_WALLETS` / `WALLET_WEIGHTS` solo las que igualen o superen el peor WR actual en ELITE (84.4%, Decu) — igual que se hizo con las wallets existentes.
5. Las que no lleguen, se descartan de `TARGET_WALLETS` — no quedan "por si acaso".

## Cambios que NO se hacen en este spec
- No se modifica `config.py`, `.env` ni variables de Railway.
- No se cambia `LIVE_MODE`.
- No se agrega Kimchi (sin wallet verificable).

# Diseño: GMGN como inteligencia offline de wallets (descubrimiento + prior)

**Fecha:** 2026-08-24
**Estado:** Aprobado
**Objetivo:** Usar la API de GMGN (gmgn.ai) para automatizar dos tareas hoy manuales — descubrir wallets candidatas nuevas y estimar su winrate/PnL real antes de gastar semanas en fase SIM — sin tocar el pipeline de detección/ejecución en tiempo real que ya funciona.

---

## Contexto y motivación

Hoy el descubrimiento de wallets objetivo es 100% manual:

1. El usuario encuentra una wallet interesante (redes, observación) y la agrega a `WALLET_LABELS` en `config.py`, en fase SIM (no entra a `ELITE_WALLETS`/`WALLET_WEIGHTS` todavía).
2. El bot la observa vía `watcher.py` (Helius WS) durante semanas, acumulando su propio historial de trades.
3. `root_wallet_miner.wallet_features()`/`initial_wallet_trust()` calculan winrate/PnL **solo con esa muestra propia**, que arranca en cero y tarda en ser significativa.
4. A mano, el usuario decide graduar la wallet a `ELITE_WALLETS`/`WALLET_WEIGHTS`, o descartarla.

El usuario encontró que **GMGN** (gmgn.ai) expone una API (vía el paquete `gmgn-skills`, backed por `gmgn-cli`) con clasificación de wallets (smart money, sniper, bundler, "rat trader") y PnL/winrate históricos reales con mucho más volumen del que el propio bot puede observar en semanas. Se evaluó usarla para:

- Descubrir candidatas nuevas automáticamente (hoy: búsqueda manual).
- Dar un prior de winrate/PnL a una wallet desde el día 1 en fase SIM (hoy: arranca sin datos).
- Cross-check periódico de wallets ya activas, para detectar degradación.

**Fuera de alcance (decidido explícitamente):**
- `gmgn-swap` y `gmgn-cooking` (ejecución financiera real vía GMGN) — no se instalan ni se integran. `executor.py` sigue siendo el único componente que toca fondos reales.
- GMGN **no participa en el camino de decisión en tiempo real** (`watcher.py` → `root_decider.py` → `executor.py`). Es inteligencia offline consultada manualmente o en scripts aparte, no una dependencia de red nueva en el hot path de cada trade.

---

## Arquitectura

### Antes
```
Usuario encuentra wallet (manual) → WALLET_LABELS (fase SIM)
        ↓
watcher.py observa en vivo (Helius WS) → acumula historial propio
        ↓ semanas después
root_wallet_miner.initial_wallet_trust() ← solo dataset propio
        ↓
Usuario gradúa a mano a ELITE_WALLETS / WALLET_WEIGHTS
```

### Después
```
scripts/gmgn_wallet_scout.py (manual, offline)
        ↓ usa
copytrade/gmgn_client.py → GMGN OpenAPI (REST, httpx) → cache data/gmgn_cache/ (TTL)
        ↓
Reporte de candidatas nuevas (smart money GMGN, excluye WALLET_LABELS ya conocidas)
        ↓
Usuario revisa y decide a mano agregar a WALLET_LABELS (fase SIM) — sin cambios en este paso

root_wallet_miner.initial_wallet_trust() ← dataset propio
        + (opcional, si hay datos GMGN) blend con gmgn_client.get_wallet_stats()
        ↓
Prior inicial mejor informado desde el día 1 de fase SIM
```

`watcher.py`, `root_decider.py`, `executor.py`: **sin cambios**.

---

## Componentes

### 1. `copytrade/gmgn_client.py` (nuevo)
Cliente HTTP delgado a la GMGN OpenAPI REST (no invoca el CLI de Node — el bot es Python, llamada directa con `httpx`, ya en `requirements.txt`).

- `get_wallet_stats(address: str, chain: str = "sol") -> dict | None` — PnL, winrate, holdings. `None` si falla o no hay `GMGN_API_KEY`.
- `get_smart_money_wallets(chain: str = "sol", limit: int = 50) -> list[dict]` — wallets clasificadas como smart money.
- Cache en disco (`data/gmgn_cache/<hash>.json`) con TTL (default 6h, configurable por env) — evita golpear rate limit y respeta el estándar de indexing+caching+async del usuario.
- **Fail-open:** cualquier error de red/API/API-key-ausente devuelve `None`/`[]` y loguea un warning — nunca lanza excepción que rompa el caller.
- Config nueva en `.env`/`config.py`: `GMGN_API_KEY` (mismo patrón que `ETHERSCAN_API_KEY`).

### 2. `scripts/gmgn_wallet_scout.py` (nuevo)
Script standalone, se corre a mano (no en el loop del bot ni en launchd).

- Llama `gmgn_client.get_smart_money_wallets()`.
- Cruza contra `config.WALLET_LABELS` (excluye ya conocidas).
- Imprime tabla: dirección, winrate GMGN, PnL, clasificación.
- No escribe en `config.py` — el usuario agrega a mano las que decida (mismo flujo de hoy).

### 3. `copytrade/root_wallet_miner.py` (extendido)
Nueva función `blend_with_gmgn(wallet_trust: dict, addresses: list[str]) -> dict`:

- Para cada address con datos GMGN disponibles, mezcla el prior propio con el winrate GMGN (promedio ponderado — peso configurable, default 50/50).
- Si GMGN no tiene datos o falla, devuelve el `wallet_trust` original sin cambios.
- Se invoca explícitamente donde se arma el seed inicial (no automático dentro de `initial_wallet_trust()`, para mantener esa función pura y testeable sin red).

---

## Manejo de errores

- Sin `GMGN_API_KEY` configurada: `gmgn_client` funciona en modo no-op (devuelve `None`/`[]`), el bot arranca igual que hoy.
- Rate limit / timeout / 4xx/5xx: se loguea, se devuelve cache si existe (aunque expirado) como fallback, si no hay cache se devuelve `None`/`[]`.
- Nunca bloquea el arranque del bot ni el ciclo de `watcher.py`/`root_decider.py` — este código no se importa desde ahí.

---

## Testing

- `gmgn_client.py`: tests con `httpx` mockeado (sin llamadas reales a la red) — casos: respuesta OK, sin API key, timeout, cache hit/miss/expirado.
- `blend_with_gmgn()`: tests puros con diccionarios fijos — sin red, sin GMGN real.
- `gmgn_wallet_scout.py`: test de la lógica de cruce/exclusión contra `WALLET_LABELS`, con `gmgn_client` mockeado.

---

## Fuera de alcance / decisiones explícitas

- No se instala ni integra `gmgn-swap` ni `gmgn-cooking` (ejecución financiera).
- No se toca `watcher.py`, `executor.py`, `root_decider.py`.
- No se automatiza la graduación de wallets a `ELITE_WALLETS`/`WALLET_WEIGHTS` — sigue siendo decisión manual del usuario, ahora informada por datos GMGN.
- Reporte periódico de degradación de wallets activas (vía `generate_report.py`/`report_whatsapp.py`) queda para una iteración futura, no en este spec.

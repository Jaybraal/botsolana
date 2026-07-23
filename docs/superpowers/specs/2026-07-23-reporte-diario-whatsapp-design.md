# Reporte diario de BotSolana por WhatsApp

## Contexto

BotSolana corre en modo SIM (`LIVE_MODE=false`), copiando 11 wallets de Solana. Hasta ahora vivía en Railway, pero el deploy está caído (`Failed`, detectado 13/07/26) y no se ha diagnosticado. El usuario decidió dejarlo corriendo localmente en su Mac en su lugar, y quiere un resumen diario de cómo va, entregado por WhatsApp, para poder copiar sus trades manualmente sin tener que revisar logs.

## Objetivo

1. El bot (`main.py`) corre local de forma estable: arranca solo, se relanza si se cae o si se reinicia el Mac.
2. El servidor WhatsApp ya existente (`CRM-auto/baileys-server`) corre local con la misma estabilidad, porque el reporte depende de él.
3. Todos los días a las 9:00 PM llega un mensaje de WhatsApp al número `18295080887` con el resumen de las últimas 24h.

## Fuera de alcance

- No se reactiva Railway ni se diagnostica el 502 de PumpPortal que lo tumbó.
- No se activa modo live (`LIVE_MODE`) ni modo autónomo — sigue en SIM / copywallet-only, sin cambios de estrategia.
- No se agregan alertas en tiempo real por cada trade — solo el resumen diario.
- No se construye una sesión de WhatsApp nueva — se reusa el `baileys-server` de CRM-auto.

## Arquitectura

Tres piezas independientes, cada una un LaunchAgent de macOS (`~/Library/LaunchAgents/`):

1. **`com.branel.botsolana.bot.plist`** — ejecuta `python3 main.py` con cwd en `/Users/branel/Desktop/botsolana`, `KeepAlive: true`, `RunAtLoad: true`. Logs a `logs/launchd_bot.log` (stdout/stderr). Reemplaza a `start_bot.sh` (ese script mata procesos previos por PID file, pensado para invocación manual; `launchd` con `KeepAlive` ya se encarga de que solo haya una instancia y de relanzar).
2. **`com.branel.baileys.server.plist`** — ejecuta `node index.js` con cwd en `/Users/branel/CRM-auto/baileys-server`, mismo patrón `KeepAlive`/`RunAtLoad`. Logs a un archivo dentro de `baileys-server/logs/`.
3. **`com.branel.botsolana.report.plist`** — `StartCalendarInterval` a las 21:00 diario, ejecuta `python3 report_whatsapp.py` en `/Users/branel/Desktop/botsolana`. No usa `KeepAlive` (es una tarea puntual, no un proceso persistente).

**Por qué LaunchAgent y no cron:** ya vamos a tener 2 procesos persistentes (bot + baileys) que necesitan auto-restart, y `launchd` es el mecanismo nativo de macOS para eso (cron no reinicia procesos caídos, solo lanza comandos a hora fija). Usar el mismo mecanismo para las 3 piezas (persistentes + programada) es más simple que mezclar cron y launchd.

**Script de reporte — `report_whatsapp.py`** (nuevo, en la raíz de `botsolana/`):
- Lee `data/sim_balance.json` (balance actual, capital inicial), `data/sim_history.json` (lista de trades cerrados, cada uno con `timestamp`, `won`, `pnl_usd`, `pnl_pct`, `wallet_label`, `symbol`), `data/sim_positions.json` (posiciones abiertas).
- Filtra `sim_history.json` a trades con `timestamp` dentro de las últimas 24h.
- Calcula: total de trades 24h, win rate 24h, P&L neto 24h en USD, mejor y peor trade del día, cantidad de posiciones abiertas, ROI total (`balance / initial - 1`).
- Chequea si el proceso del bot está vivo (`pgrep -f "python3 main.py"` o equivalente) para avisar si se cayó — es la única señal de salud que realmente importa para el usuario.
- Arma un mensaje de texto plano (con emojis, igual que los logs existentes) y hace `POST http://localhost:3002/send` con body `{ to: "18295080887", text, sessionId: "<orgId conectado>" }`.
- Si el POST falla (baileys caído, sesión no conectada, error de red): loguea el fallo en un archivo local (`logs/report_failures.log`) y termina sin reintentar — no hay nadie más a quien avisar si WhatsApp mismo es el canal caído.

## Formato del mensaje

```
🤖 BotSolana — Reporte {fecha} {hora}
Estado: {🟢 corriendo | 🔴 proceso caído}
Balance: ${balance} (inicial ${initial} → ROI {roi}%)
Últimas 24h: {n} trades | WR {wr}% ({wins}W/{losses}L) | P&L ${pnl_24h}
Mejor: {symbol} {wallet_label} +{pct}%
Peor: {symbol} {wallet_label} {pct}%
Posiciones abiertas: {n}
```
Si no hubo trades en 24h, se omiten las líneas de mejor/peor y se indica "sin actividad".

## Setup manual requerido (una sola vez)

✅ Hecho el 23/07/26: la sesión de WhatsApp de `baileys-server` no tenía ninguna cuenta vinculada (`registered: false`); se generó el QR vía `/connect/HzyDWOrkySUVdcoYUszi` + `/qr/HzyDWOrkySUVdcoYUszi`, el usuario lo escaneó, y se confirmó `status: "open", connected: true` con un mensaje de prueba real entregado al `18295080887`. `sessionId` para todos los envíos = `HzyDWOrkySUVdcoYUszi` (el `orgId` ya configurado en `baileys-server/.env`).

## Testing

- Test unitario de la función que calcula el resumen 24h a partir de un `sim_history.json` de prueba (casos: sin trades, solo wins, solo losses, mixto, trades fuera de la ventana de 24h).
- Test de formato del mensaje (dado un resumen calculado, el texto generado coincide con el template).
- El envío real por WhatsApp y los LaunchAgents se verifican manualmente (no son unit-testeables sin mockear todo el sistema operativo/red) — se confirma con `launchctl list` + un envío de prueba real al número del usuario.

## Riesgos conocidos

- Los datos de `sim_history.json`/`sim_balance.json` llevan desde el 7/07 sin actualizar (bot parado) — el primer reporte reflejará eso hasta que el bot vuelva a correr y generar trades nuevos.
- Si el Mac está apagado o dormido a las 9:00 PM, `launchd` no dispara el job (no hay wake-on-schedule configurado); se pierde el reporte de ese día, no hay backfill.

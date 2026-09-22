# Bloqueador 4 — Railway Container (Auditado 2026-07-01)

## Problema
- Container parado desde ~23:48 (ayer)
- WebSocket error 502: conexión cerrada
- NO puedo reiniciar vía CLI/Bash (requiere acceso dashboard)

## Estado Actual (01/07/2026)
- **Error último:** `WebSocket error 502`
- **Logs esperados:** `Connected to PumpPortal` (ausente)
- **GROQ_API_KEY:** ✅ Configurada en Railway, `USE_GROQ_SCORER=true`
- **Cambios:** Commiteados a `main`, push realizado

## Pasos Manuales — Reinicio Container

### 1. Abre Dashboard Railway
```
https://railway.app/project/botsolana
```

### 2. Selecciona Servicio `botsolana`
- Navega a la pestaña "Deployments" o "Logs"
- Asegúrate de que estés en el servicio correcto (no `server` ni otro)

### 3. Detén el Container
- Botón **STOP** (rojo)
- Espera a que el estado cambie a `Stopped` (suele tardar 10-15s)

### 4. Reinicia Automáticamente o Manual
- **Opción A (Auto):** Railway reinicia automáticamente tras 30-60s
- **Opción B (Manual):** Presiona botón **START** (verde)

### 5. Verifica Logs de Conexión
Espera a que aparezcan logs:
```
[botsolana] Connected to PumpPortal
[botsolana] Listening on port 5000
```

Sin error 502 = ✅ Conexión exitosa

### 6. Prueba WebSocket (Opcional)
```bash
# Desde terminal local:
curl -i http://botsolana-production.up.railway.app/health

# Respuesta esperada:
# HTTP/1.1 200 OK
# {"status":"online"}
```

## Troubleshooting

### Si sigue con error 502 tras reinicio
1. Verifica `GROQ_API_KEY` en Railway → Variables
2. Verifica que `autonomous_scanner.py` está actualizado (commit d99ebae)
3. Revisa logs de error: ¿hay error de módulo Python?
4. Último recurso: Force deploy
   - Botón "Deploy" → selecciona commit `d99ebae`
   - Espera a que compile y reinicie

### Si el container no arranca
- Revisa que `main` branch esté sincronizado con GitHub
- Verifica `package.json` (Node.js) o `requirements.txt` (Python)
- Check Railway build logs para errores de dependencias

## Variables Críticas en Railway
```
GROQ_API_KEY=sk-proj-...                    # ✅ Requerida para scorer
USE_GROQ_SCORER=true                         # ✅ Activa scorer
SIM_MAX_HOLD_MIN=30                          # ✅ Fix 2026-07-01
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
PORT=5000
```

## Próximos Pasos
1. ✅ Confirmar reinicio manual exitoso (botón START en Railway)
2. Monitorear logs: sin error 502 por 5+ minutos = OK
3. Si todo OK: Marcar como resuelto
4. Opcional: Configurar alertas en Railway para downtime

---

**Audit Date:** 2026-07-01  
**Status:** PAUSED (awaiting manual restart)  
**Commit:** `d99ebae` (fix SIM_MAX_HOLD_MIN + audit)

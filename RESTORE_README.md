# 🆘 EMERGENCIA — Cómo revertir rápido

## Si algo falla y necesitas volver al estado del 6 mayo 21:35 UTC

---

## OPCIÓN 1: Script automático (RECOMENDADO - 2 min)

```bash
cd /Users/branel/Desktop/botsolana
bash RESTORE.sh
```

✓ Vuelve código + variables + redeploy automático
✓ Más seguro (pide confirmación)
✓ Te muestra qué está pasando

---

## OPCIÓN 2: Comandos manuales (si el script falla)

### Paso 1: Revertir código
```bash
cd /Users/branel/Desktop/botsolana
git reset --hard a1bceaa
git push -f origin main
```

### Paso 2: Restaurar variables (copiar y pegar)
```bash
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

### Paso 3: Esperar redeploy
```bash
railway logs --tail 20
# Espera a que diga: "Ready to accept connections" o similar
```

---

## OPCIÓN 3: Si todo falla, contacta y te ayudo

Avisame exactamente qué error ves, y revertimos juntos.

---

## Qué está guardado

```
✓ BACKUP_CONFIG_20260506.md      ← Configuración exacta
✓ RESTORE.sh                      ← Script automático
✓ commit a1bceaa en GitHub        ← Código original
✓ Variables en Railway            ← Respaldo documentado
```

---

## Verificar que funcionó

```bash
# Ver que el commit es a1bceaa
git log --oneline -1

# Ver variables
railway variables | grep SIM_SLIPPAGE_PCT
# Debe mostrar: 0.015

# Ver logs en vivo
railway logs --tail 30
```

Si ves `SIM_SLIPPAGE_PCT = 0.015` → **restauración completada** ✓

---

## No borres estos archivos

```
❌ NO BORRES:
  - BACKUP_CONFIG_20260506.md
  - RESTORE.sh
  - RESTORE_README.md (este)
  
✓ Están en .gitignore (no se suben a GitHub)
✓ Son tu punto de restauración
```

---

**Guardado:** 6 mayo 2026 21:35 UTC
**Commit a restaurar:** `a1bceaa`
**Tiempo de restauración:** ~2 minutos

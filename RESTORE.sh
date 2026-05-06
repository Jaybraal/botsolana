#!/bin/bash
# 🔄 RESTORE SCRIPT — Volver a configuración del 6 mayo 2026 21:35 UTC
# Uso: bash RESTORE.sh

set -e  # Exit if any command fails

echo "⚠️  RESTAURANDO A CONFIGURACIÓN DEL 6 MAYO 2026..."
echo ""

# Paso 1: Verificar que estamos en el repo correcto
if [ ! -d ".git" ]; then
    echo "❌ ERROR: No estamos en un git repository"
    echo "Ejecuta este script desde /Users/branel/Desktop/botsolana/"
    exit 1
fi

echo "✓ Verificado: estamos en git repository"
echo ""

# Paso 2: Mostrar estado actual
echo "📊 Estado actual:"
git log --oneline -3
echo ""

# Paso 3: Confirmar que el usuario quiere revertir
read -p "¿Revertir a commit a1bceaa (6 mayo 21:35)? (s/n): " -r
if [[ ! $REPLY =~ ^[Ss]$ ]]; then
    echo "❌ Abortado"
    exit 1
fi
echo ""

# Paso 4: Revertir git
echo "🔄 Revirtiendo código a commit a1bceaa..."
git reset --hard a1bceaa
if [ $? -eq 0 ]; then
    echo "✓ Código revertido"
else
    echo "❌ Error revirtiendo código"
    exit 1
fi
echo ""

# Paso 5: Force push a GitHub
echo "🔄 Forzando push a GitHub..."
git push -f origin main
if [ $? -eq 0 ]; then
    echo "✓ GitHub actualizado"
else
    echo "❌ Error en push"
    exit 1
fi
echo ""

# Paso 6: Restaurar variables de Railway
echo "🔄 Restaurando variables de Railway..."
railway variables set LIVE_MODE=false
railway variables set SIM_CAPITAL=50
railway variables set SIM_SLIPPAGE_PCT=0.015
railway variables set SIM_PRIORITY_FEE_SOL=0.0004
railway variables set MAX_SESSION_LOSS_PCT=0.20
railway variables set MAX_TRADE_PCT=0.10
railway variables set MIN_TRADE_SOL=0.001
railway variables set MAX_OPEN_COPIES=3
railway variables set DEPLOY_TS=$(date +%s)

echo "✓ Variables de Railway restauradas"
echo ""

# Paso 7: Confirmación final
echo "✅ RESTAURACIÓN COMPLETADA"
echo ""
echo "Estado actual:"
git log --oneline -1
echo ""
echo "Railway está redeploy-ando (espera 30 segundos)..."
echo "Verifica con: railway logs --tail 20"

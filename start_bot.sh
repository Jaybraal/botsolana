#!/bin/bash
# Script para iniciar BotSolana de forma estable con nohup

REPO_DIR="/Users/branel/Desktop/botsolana"
PID_FILE="/tmp/botsolana.pid"
LOG_FILE="/tmp/botsolana_stable.log"

cd "$REPO_DIR" || exit 1

# Matar proceso anterior si existe
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "⚠️  Deteniendo proceso anterior (PID $OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null
        sleep 2
    fi
fi

# Iniciar bot con nohup (immune a desconexiones)
echo "🚀 Iniciando BotSolana..."
nohup python3 main.py > "$LOG_FILE" 2>&1 &
BOT_PID=$!
echo "$BOT_PID" > "$PID_FILE"

# Verificar que arrancó correctamente
sleep 2
if ps -p "$BOT_PID" > /dev/null; then
    echo "✅ Bot arrancado correctamente con PID $BOT_PID"
    echo "📊 Monitorea el progreso con:"
    echo "   tail -f $LOG_FILE"
    echo ""
    echo "⏹️  Para detener el bot:"
    echo "   kill $BOT_PID"
else
    echo "❌ Error: Bot no se inició"
    exit 1
fi

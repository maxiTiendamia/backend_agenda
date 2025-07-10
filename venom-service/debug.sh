#!/bin/bash

# Script de diagnóstico para depurar problemas de sesiones WhatsApp
# Usar: docker exec -it [container_name] /app/debug.sh

echo "🔍 DIAGNÓSTICO DE SESIONES WHATSAPP"
echo "==================================="
echo "Timestamp: $(date)"
echo ""

echo "📁 ESTRUCTURA DE CARPETAS:"
echo "tokens/ directorio:"
ls -la /app/tokens/ 2>/dev/null || echo "❌ No existe /app/tokens/"
echo ""

echo "Subdirectorios en tokens/:"
for dir in /app/tokens/*/; do
    if [ -d "$dir" ]; then
        clienteId=$(basename "$dir")
        echo "  📂 Cliente $clienteId:"
        echo "    - Archivos: $(ls "$dir" | wc -l)"
        
        if [ -d "$dir/Default" ]; then
            echo "    - Default/ existe: ✅"
            echo "    - Archivos en Default: $(ls "$dir/Default" | wc -l)"
            
            # Verificar archivos críticos
            if ls "$dir/Default" | grep -i "local storage" > /dev/null; then
                echo "    - Local Storage: ✅"
            else
                echo "    - Local Storage: ❌"
            fi
            
            if ls "$dir/Default" | grep -i "preferences" > /dev/null; then
                echo "    - Preferences: ✅"
            else
                echo "    - Preferences: ❌"
            fi
        else
            echo "    - Default/ existe: ❌"
        fi
        
        # Verificar SingletonLock
        if [ -f "$dir/SingletonLock" ]; then
            echo "    - SingletonLock: ⚠️ (presente)"
        else
            echo "    - SingletonLock: ✅ (no presente)"
        fi
        echo ""
    fi
done

echo "🔐 ARCHIVOS SINGLETONLOCK:"
find /app/tokens -name "SingletonLock" -type f 2>/dev/null | while read lock; do
    echo "  - $lock (tamaño: $(stat -c%s "$lock" 2>/dev/null || echo "unknown"))"
done
echo ""

echo "🌐 ESTADO DEL SERVICIO:"
echo "Puerto 3000 en uso:"
netstat -tuln | grep :3000 || echo "❌ Puerto 3000 no está en uso"
echo ""

echo "📊 PROCESOS NODE:"
ps aux | grep node || echo "❌ No hay procesos node corriendo"
echo ""

echo "💾 USO DE MEMORIA:"
free -h
echo ""

echo "💿 ESPACIO EN DISCO:"
df -h /app
echo ""

echo "📝 LOGS RECIENTES (últimas 20 líneas):"
tail -20 /var/log/app.log 2>/dev/null || echo "❌ No se encontró /var/log/app.log"
echo ""

echo "🔗 ENDPOINTS DISPONIBLES:"
echo "  - GET /diagnostico          - Diagnóstico general"
echo "  - GET /diagnostico/:id      - Diagnóstico específico"
echo "  - POST /limpiar/:id         - Limpiar sesión específica"
echo "  - POST /reparar-automatico/:id - Reparar automáticamente"
echo "  - GET /iniciar/:id          - Iniciar nueva sesión"
echo "  - GET /qr/:id              - Ver QR"
echo ""

echo "✅ Diagnóstico completado"

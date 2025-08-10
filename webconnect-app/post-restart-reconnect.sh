#!/bin/bash

# Script de reconexión post-restart para WebConnect
# Este script debe ejecutarse después de reiniciar el VPS o PM2

echo "🚀 Iniciando script de reconexión post-restart..."

# Función para verificar si el servicio está corriendo
check_service() {
    echo "🔍 Verificando estado del servicio..."
    
    # Verificar si PM2 está corriendo
    if ! command -v pm2 &> /dev/null; then
        echo "❌ PM2 no está instalado"
        return 1
    fi
    
    # Verificar si la aplicación está corriendo
    if pm2 list | grep -q "webconnect-agenda"; then
        echo "✅ WebConnect está corriendo en PM2"
        return 0
    else
        echo "❌ WebConnect no está corriendo"
        return 1
    fi
}

# Función para esperar que el servicio esté listo
wait_for_service() {
    echo "⏳ Esperando que el servicio esté listo..."
    
    for i in {1..12}; do  # 60 segundos máximo (12 x 5)
        if curl -s http://localhost:3000/health-check > /dev/null; then
            echo "✅ Servicio está listo"
            return 0
        fi
        echo "⏳ Esperando... ($i/12)"
        sleep 5
    done
    
    echo "❌ Timeout esperando que el servicio esté listo"
    return 1
}

# Función para forzar reconexión
force_reconnect() {
    echo "🔄 Forzando reconexión de todas las sesiones..."
    
    response=$(curl -s -X POST http://localhost:3000/force-reconnect-all)
    
    if echo "$response" | grep -q '"ok":true'; then
        echo "✅ Reconexión forzada exitosa"
        echo "$response" | jq '.' 2>/dev/null || echo "$response"
        return 0
    else
        echo "❌ Error en reconexión forzada"
        echo "$response"
        return 1
    fi
}

# Función para verificar estado después de reconexión
check_health() {
    echo "🏥 Verificando estado de salud de las sesiones..."
    
    response=$(curl -s http://localhost:3000/health-check)
    
    if echo "$response" | grep -q '"ok":true'; then
        echo "✅ Estado de salud obtenido"
        echo "$response" | jq '.estadoGeneral' 2>/dev/null || echo "$response"
        
        # Verificar si hay sesiones desconectadas
        if echo "$response" | grep -q '"sesionesDesconectadas":0'; then
            echo "🎉 Todas las sesiones están conectadas correctamente"
            return 0
        else
            echo "⚠️ Hay sesiones desconectadas - Se necesita intervención manual"
            return 1
        fi
    else
        echo "❌ Error verificando estado de salud"
        echo "$response"
        return 1
    fi
}

# Función principal
main() {
    echo "==============================================="
    echo "🔧 WebConnect Post-Restart Reconnection Script"
    echo "==============================================="
    echo "Timestamp: $(date)"
    echo ""
    
    # Verificar servicio
    if ! check_service; then
        echo "❌ El servicio no está corriendo. Iniciando con PM2..."
        cd "$(dirname "$0")"
        pm2 start ecosystem.config.json
        sleep 10
    fi
    
    # Esperar a que esté listo
    if ! wait_for_service; then
        echo "❌ El servicio no está respondiendo correctamente"
        exit 1
    fi
    
    echo ""
    echo "📊 Estado inicial:"
    curl -s http://localhost:3000/health-check | jq '.estadoGeneral' 2>/dev/null || echo "Error obteniendo estado inicial"
    echo ""
    
    # Forzar reconexión
    if force_reconnect; then
        echo ""
        echo "⏳ Esperando 15 segundos para que las reconexiones se establezcan..."
        sleep 15
        
        # Verificar estado final
        echo ""
        echo "📊 Estado final:"
        check_health
        
        if [ $? -eq 0 ]; then
            echo ""
            echo "🎉 Reconexión post-restart completada exitosamente"
            echo "==============================================="
            exit 0
        else
            echo ""
            echo "⚠️ Reconexión completada pero con advertencias"
            echo "Se recomienda revisar manualmente las sesiones"
            echo "==============================================="
            exit 1
        fi
    else
        echo ""
        echo "❌ Error durante la reconexión forzada"
        echo "==============================================="
        exit 1
    fi
}

# Verificar si curl está disponible
if ! command -v curl &> /dev/null; then
    echo "❌ curl no está instalado. Se requiere para este script."
    exit 1
fi

# Ejecutar función principal
main "$@"

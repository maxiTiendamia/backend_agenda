#!/bin/bash

# Script de prueba para verificar que las mejoras funcionen correctamente
# Usar después de implementar los cambios

BASE_URL="http://localhost:3000"
CLIENTE_PRUEBA="35"

echo "🧪 SCRIPT DE PRUEBA - MEJORAS SESIONES WHATSAPP"
echo "================================================"
echo "Base URL: $BASE_URL"
echo "Cliente de prueba: $CLIENTE_PRUEBA"
echo ""

# Función para hacer peticiones HTTP y mostrar resultado
hacer_peticion() {
    local metodo="$1"
    local endpoint="$2"
    local descripcion="$3"
    
    echo "🔍 $descripcion"
    echo "   $metodo $BASE_URL$endpoint"
    
    if [ "$metodo" = "GET" ]; then
        response=$(curl -s -w "\nHTTP_CODE:%{http_code}" "$BASE_URL$endpoint")
    else
        response=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X "$metodo" "$BASE_URL$endpoint")
    fi
    
    http_code=$(echo "$response" | grep "HTTP_CODE:" | cut -d: -f2)
    body=$(echo "$response" | sed '/HTTP_CODE:/d')
    
    if [ "$http_code" -eq 200 ]; then
        echo "   ✅ Success ($http_code)"
        echo "$body" | jq . 2>/dev/null || echo "$body"
    else
        echo "   ❌ Error ($http_code)"
        echo "$body"
    fi
    echo ""
}

echo "1️⃣ DIAGNÓSTICO GENERAL"
hacer_peticion "GET" "/diagnostico" "Obtener diagnóstico general del sistema"

echo "2️⃣ DIAGNÓSTICO ESPECÍFICO"
hacer_peticion "GET" "/diagnostico/$CLIENTE_PRUEBA" "Diagnosticar cliente $CLIENTE_PRUEBA específicamente"

echo "3️⃣ LIMPIEZA DE SESIÓN"
hacer_peticion "POST" "/limpiar/$CLIENTE_PRUEBA" "Limpiar completamente la sesión del cliente $CLIENTE_PRUEBA"

echo "4️⃣ VERIFICAR LIMPIEZA"
hacer_peticion "GET" "/diagnostico/$CLIENTE_PRUEBA" "Verificar que la limpieza fue exitosa"

echo "5️⃣ INICIAR NUEVA SESIÓN"
hacer_peticion "GET" "/iniciar/$CLIENTE_PRUEBA" "Iniciar nueva sesión limpia para cliente $CLIENTE_PRUEBA"

echo "6️⃣ VERIFICAR QR GENERADO"
echo "🔍 Verificar si el QR se generó correctamente"
sleep 3
curl -s -I "$BASE_URL/qr/$CLIENTE_PRUEBA" | head -1
echo ""

echo "7️⃣ DIAGNÓSTICO FINAL"
hacer_peticion "GET" "/diagnostico/$CLIENTE_PRUEBA" "Diagnóstico final después de iniciar nueva sesión"

echo "✅ PRUEBA COMPLETADA"
echo ""
echo "📋 RESUMEN DE ENDPOINTS IMPORTANTES:"
echo "   - GET /diagnostico/:id           - Ver estado detallado"
echo "   - POST /limpiar/:id             - Limpiar sesión corrupta"  
echo "   - POST /reparar-automatico/:id  - Limpiar + iniciar automáticamente"
echo "   - GET /iniciar/:id              - Iniciar nueva sesión"
echo "   - GET /qr/:id                   - Ver código QR"
echo ""
echo "🔧 COMANDOS DE DOCKER ÚTILES:"
echo "   docker logs [container] --tail 50    - Ver logs recientes"
echo "   docker exec -it [container] ./debug.sh - Ejecutar diagnóstico interno"
echo "   docker exec -it [container] /bin/bash  - Acceso al contenedor"

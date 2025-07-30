// Archivo principal de arranque para WebConnect
require('dotenv').config();
const express = require('express');
const Redis = require('ioredis');
const { Pool } = require('pg'); // Cambiar a PostgreSQL
const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());

// Configuración de Redis
const redis = new Redis(process.env.REDIS_URL || 'rediss://default:AcOQAAIjcDEzOGI2OWU1MzYxZDQ0YWQ2YWU3ODJlNWNmMGY5MjIzY3AxMA@literate-toucan-50064.upstash.io:6379');

// Configuración de PostgreSQL usando la URL completa
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: {
    rejectUnauthorized: false // Necesario para Render
  }
});

/**
 * Función para limpiar datos obsoletos en Redis comparando con la base de datos
 */
async function limpiarDatosObsoletos() {
  let dbClient = null;
  
  try {
    console.log('[CLEANUP] 🧹 Iniciando limpieza de datos obsoletos...');
    
    // 1. Conectar a la base de datos PostgreSQL
    dbClient = await pool.connect();
    console.log('[CLEANUP] ✅ Conectado a la base de datos PostgreSQL');
    
    // 2. Verificar estructura de la tabla tenants
    const tableInfo = await dbClient.query(`
      SELECT column_name, data_type 
      FROM information_schema.columns 
      WHERE table_name = 'tenants'
      ORDER BY ordinal_position
    `);
    
    console.log('[CLEANUP] 📋 Columnas de la tabla tenants:');
    tableInfo.rows.forEach(col => {
      console.log(`[CLEANUP]   - ${col.column_name} (${col.data_type})`);
    });
    
    // 3. Obtener todos los IDs de tenants (sin filtro de estado ya que no hay columna activo)
    // Simplemente obtener todos los tenants existentes
    const query = 'SELECT id FROM tenants';
    console.log(`[CLEANUP] 🔍 Ejecutando consulta: ${query}`);
    
    const result = await dbClient.query(query);
    const tenantsActivos = result.rows.map(tenant => tenant.id.toString());
    console.log(`[CLEANUP] 📊 Tenants en BD: ${tenantsActivos.length} encontrados`);
    console.log(`[CLEANUP] 📋 IDs encontrados: [${tenantsActivos.join(', ')}]`);
    
    // 4. Obtener todas las claves en Redis relacionadas con sesiones
    const redisKeys = await redis.keys('*');
    console.log(`[CLEANUP] 🔍 Claves en Redis: ${redisKeys.length} encontradas`);
    
    // 5. Filtrar claves que parecen ser de sesiones/clientes
    const sessionKeys = redisKeys.filter(key => {
      // Buscar patrones comunes de claves de sesión
      return key.includes('session_') || 
             key.includes('client_') || 
             key.includes('qr_') || 
             key.includes('whatsapp_') ||
             key.includes('tenant_') ||
             /^\d+$/.test(key); // Claves que son solo números (IDs)
    });
    
    console.log(`[CLEANUP] 🎯 Claves de sesión encontradas: ${sessionKeys.length}`);
    console.log(`[CLEANUP] 🔍 Claves encontradas: [${sessionKeys.join(', ')}]`);
    
    // Si no hay tenants o no hay claves de sesión, no hacer nada
    if (tenantsActivos.length === 0) {
      console.log('[CLEANUP] ⚠️ No hay tenants en la base de datos');
      return { clavesValidas: 0, clavesEliminadas: 0, detalles: { validas: [], eliminadas: [] } };
    }
    
    if (sessionKeys.length === 0) {
      console.log('[CLEANUP] ✨ No hay claves de sesión en Redis');
      return { clavesValidas: 0, clavesEliminadas: 0, detalles: { validas: [], eliminadas: [] } };
    }
    
    // 6. Revisar cada clave y extraer el ID del cliente
    let clavesObsoletas = [];
    let clavesValidas = [];
    
    for (const key of sessionKeys) {
      // Extraer ID del cliente de diferentes formatos de clave
      let clienteId = null;
      
      if (key.includes('session_')) {
        clienteId = key.replace('session_', '').split('_')[0];
      } else if (key.includes('client_')) {
        clienteId = key.replace('client_', '').split('_')[0];
      } else if (key.includes('qr_')) {
        clienteId = key.replace('qr_', '').split('_')[0];
      } else if (key.includes('whatsapp_')) {
        clienteId = key.replace('whatsapp_', '').split('_')[0];
      } else if (key.includes('tenant_')) {
        clienteId = key.replace('tenant_', '').split('_')[0];
      } else if (/^\d+$/.test(key)) {
        clienteId = key;
      } else {
        // Intentar extraer números del inicio de la clave
        const match = key.match(/^(\d+)/);
        if (match) {
          clienteId = match[1];
        }
      }
      
      if (clienteId) {
        // Verificar si el cliente existe en la base de datos
        if (tenantsActivos.includes(clienteId)) {
          clavesValidas.push(key);
          console.log(`[CLEANUP] ✅ Clave válida: ${key} (Cliente ID: ${clienteId})`);
        } else {
          clavesObsoletas.push(key);
          console.log(`[CLEANUP] 🗑️ Clave obsoleta: ${key} (Cliente ID: ${clienteId} no existe en BD)`);
        }
      } else {
        console.log(`[CLEANUP] ⚠️ No se pudo extraer ID de la clave: ${key}`);
      }
    }
    
    // 7. Eliminar claves obsoletas de Redis
    if (clavesObsoletas.length > 0) {
      console.log(`[CLEANUP] 🗑️ Eliminando ${clavesObsoletas.length} claves obsoletas...`);
      
      for (const key of clavesObsoletas) {
        try {
          await redis.del(key);
          console.log(`[CLEANUP] ❌ Eliminada: ${key}`);
        } catch (delError) {
          console.error(`[CLEANUP] Error eliminando clave ${key}:`, delError);
        }
      }
    } else {
      console.log(`[CLEANUP] ✨ No hay claves obsoletas para eliminar`);
    }
    
    // 8. Resumen final
    console.log(`[CLEANUP] 📊 Resumen de limpieza:`);
    console.log(`[CLEANUP] ✅ Claves válidas mantenidas: ${clavesValidas.length}`);
    console.log(`[CLEANUP] 🗑️ Claves obsoletas eliminadas: ${clavesObsoletas.length}`);
    console.log(`[CLEANUP] 🧹 Limpieza completada exitosamente`);
    
    return {
      clavesValidas: clavesValidas.length,
      clavesEliminadas: clavesObsoletas.length,
      detalles: {
        validas: clavesValidas,
        eliminadas: clavesObsoletas
      }
    };
    
  } catch (error) {
    console.error('[CLEANUP] ❌ Error durante la limpieza:', error);
    throw error;
  } finally {
    // Liberar conexión a la base de datos
    if (dbClient) {
      dbClient.release();
      console.log('[CLEANUP] 🔌 Conexión a BD liberada');
    }
  }
}

/**
 * Función para ejecutar limpieza periódica
 */
function programarLimpiezaPeriodica() {
  // Ejecutar limpieza cada 6 horas (6 * 60 * 60 * 1000 ms)
  const intervalo = 6 * 60 * 60 * 1000;
  
  setInterval(async () => {
    try {
      console.log('[CLEANUP] ⏰ Ejecutando limpieza periódica programada...');
      await limpiarDatosObsoletos();
    } catch (error) {
      console.error('[CLEANUP] ❌ Error en limpieza periódica:', error);
    }
  }, intervalo);
  
  console.log(`[CLEANUP] ⏰ Limpieza periódica programada cada ${intervalo / (60 * 60 * 1000)} horas`);
}

// Rutas
const webconnectRoutes = require('./routes/webconnectRoutes');
app.use('/', webconnectRoutes);

app.get('/health', (req, res) => {
  res.send('Service is running');
});

// Endpoint para ejecutar limpieza manual
app.post('/cleanup', async (req, res) => {
  try {
    console.log('[CLEANUP] 🔧 Limpieza manual solicitada...');
    const resultado = await limpiarDatosObsoletos();
    res.json({
      success: true,
      message: 'Limpieza completada exitosamente',
      resultado
    });
  } catch (error) {
    console.error('[CLEANUP] ❌ Error en limpieza manual:', error);
    res.status(500).json({
      success: false,
      error: 'Error durante la limpieza',
      details: error.message
    });
  }
});

// Endpoint para obtener estadísticas de Redis
app.get('/redis-stats', async (req, res) => {
  try {
    const keys = await redis.keys('*');
    const sessionKeys = keys.filter(key => {
      return key.includes('session_') || 
             key.includes('client_') || 
             key.includes('qr_') || 
             key.includes('whatsapp_') ||
             key.includes('tenant_') ||
             /^\d+$/.test(key);
    });
    
    res.json({
      totalKeys: keys.length,
      sessionKeys: sessionKeys.length,
      keys: sessionKeys
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

const { createSession, testAPIConnection, initializeExistingSessions } = require('./app/wppconnect');

// Función de inicialización
async function inicializar() {
  try {
    // 1. Probar conexión con PostgreSQL
    console.log('[INIT] 🔌 Probando conexión con PostgreSQL...');
    const client = await pool.connect();
    await client.query('SELECT NOW()');
    client.release();
    console.log('[INIT] ✅ Conexión con PostgreSQL exitosa');
    
    // 2. Probar conexión con la API
    console.log('[INIT] 🚀 Probando conexión con API...');
    await testAPIConnection();
    
    // 3. Restaurar sesiones existentes
    console.log('[INIT] 📱 Restaurando sesiones existentes...');
    await initializeExistingSessions();
    
    // 4. Ejecutar limpieza inicial
    console.log('[INIT] 🧹 Ejecutando limpieza inicial...');
    await limpiarDatosObsoletos();
    
    // 5. Programar limpieza periódica
    programarLimpiezaPeriodica();
    
    console.log('[INIT] ✅ Aplicación inicializada correctamente');
    
  } catch (error) {
    console.error('[INIT] ❌ Error durante la inicialización:', error);
  }
}

// Iniciar servidor
app.listen(PORT, async () => {
  console.log(`Server is running on port ${PORT}`);
  
  // Ejecutar inicialización después de que el servidor esté corriendo
  await inicializar();
});

// Manejo de cierre graceful
process.on('SIGTERM', async () => {
  console.log('[CLEANUP] 🛑 Cerrando aplicación...');
  await redis.disconnect();
  await pool.end();
  process.exit(0);
});

process.on('SIGINT', async () => {
  console.log('[CLEANUP] 🛑 Cerrando aplicación...');
  await redis.disconnect();
  await pool.end();
  process.exit(0);
});
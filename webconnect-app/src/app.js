// Archivo principal de arranque para WebConnect
require('dotenv').config();
const express = require('express');
const Redis = require('ioredis');
const { pool } = require('./app/database'); // ✅ Usar el pool existente
const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());

// Configuración de Redis usando redisClient
const redis = require('./app/redisClient');

/**
 * Función para limpiar datos obsoletos en Redis y directorios de tokens
 */
async function limpiarDatosObsoletos() {
  let dbClient = null;
  
  try {
    console.log('[CLEANUP] 🧹 Iniciando limpieza de datos obsoletos...');
    
    // 1. Conectar a la base de datos PostgreSQL
    dbClient = await pool.connect();
    console.log('[CLEANUP] ✅ Conectado a la base de datos PostgreSQL');
    
    // 2. Obtener todos los IDs de tenants activos
    const result = await dbClient.query('SELECT id FROM tenants');
    const tenantsActivos = result.rows.map(tenant => tenant.id.toString());
    console.log(`[CLEANUP] 📊 Tenants en BD: ${tenantsActivos.length} encontrados`);
    console.log(`[CLEANUP] 📋 IDs encontrados: [${tenantsActivos.join(', ')}]`);
    
    // 3. Limpiar Redis (código existente)
    const redisKeys = await redis.keys('*');
    console.log(`[CLEANUP] 🔍 Claves en Redis: ${redisKeys.length} encontradas`);
    
    const sessionKeys = redisKeys.filter(key => {
      return key.includes('session_') || 
             key.includes('client_') || 
             key.includes('qr_') || 
             key.includes('whatsapp_') ||
             key.includes('tenant_') ||
             /^\d+$/.test(key);
    });
    
    console.log(`[CLEANUP] 🎯 Claves de sesión encontradas: ${sessionKeys.length}`);
    
    let clavesObsoletas = [];
    let clavesValidas = [];
    
    for (const key of sessionKeys) {
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
        const match = key.match(/^(\d+)/);
        if (match) {
          clienteId = match[1];
        }
      }
      
      if (clienteId) {
        if (tenantsActivos.includes(clienteId)) {
          clavesValidas.push(key);
        } else {
          clavesObsoletas.push(key);
        }
      }
    }
    
    // Eliminar claves obsoletas de Redis
    if (clavesObsoletas.length > 0) {
      console.log(`[CLEANUP] 🗑️ Eliminando ${clavesObsoletas.length} claves obsoletas de Redis...`);
      
      for (const key of clavesObsoletas) {
        try {
          await redis.del(key);
          console.log(`[CLEANUP] ❌ Clave eliminada: ${key}`);
        } catch (delError) {
          console.error(`[CLEANUP] Error eliminando clave ${key}:`, delError);
        }
      }
    }
    
    // 4. NUEVO: Limpiar directorios de tokens obsoletos
  const fs = require('fs');
  const path = require('path');
  // Unificar ruta con wppconnect.js -> tokens está en webconnect-app/tokens
  const tokensDir = path.join(__dirname, '../tokens');
    
    // 🔧 INICIALIZAR VARIABLE AQUÍ
    let directoriosObsoletos = [];
    
    if (fs.existsSync(tokensDir)) {
      const sessionDirs = fs.readdirSync(tokensDir)
        .filter(dir => dir.startsWith('session_'))
        .map(dir => dir.replace('session_', ''));

      console.log(`[CLEANUP] 📁 Directorios de sesión encontrados: [${sessionDirs.join(', ')}]`);
      
      // 🔧 ASIGNAR VALOR AQUÍ
      directoriosObsoletos = sessionDirs.filter(sessionId => !tenantsActivos.includes(sessionId));
      
      if (directoriosObsoletos.length > 0) {
        console.log(`[CLEANUP] 🗑️ Eliminando ${directoriosObsoletos.length} directorios obsoletos...`);
        
        for (const sessionId of directoriosObsoletos) {
          try {
            const sessionDir = path.join(tokensDir, `session_${sessionId}`);
            fs.rmSync(sessionDir, { recursive: true, force: true });
            console.log(`[CLEANUP] ❌ Directorio eliminado: session_${sessionId}`);
          } catch (delError) {
            console.error(`[CLEANUP] Error eliminando directorio session_${sessionId}:`, delError);
          }
        }
      } else {
        console.log(`[CLEANUP] ✅ No hay directorios obsoletos para eliminar`);
      }
    } else {
      console.log(`[CLEANUP] 📁 No existe directorio de tokens`);
    }
    
    // 5. Resumen final
    console.log(`[CLEANUP] 📊 Resumen de limpieza:`);
    console.log(`[CLEANUP] ✅ Claves válidas mantenidas: ${clavesValidas.length}`);
    console.log(`[CLEANUP] 🗑️ Claves obsoletas eliminadas: ${clavesObsoletas.length}`);
    console.log(`[CLEANUP] 🗑️ Directorios obsoletos eliminados: ${directoriosObsoletos.length}`);
    console.log(`[CLEANUP] 🧹 Limpieza completada exitosamente`);
    
    return {
      clavesValidas: clavesValidas.length,
      clavesEliminadas: clavesObsoletas.length,
      directoriosEliminados: directoriosObsoletos.length,
      detalles: {
        validas: clavesValidas,
        eliminadas: clavesObsoletas,
        directoriosEliminados: directoriosObsoletos
      }
    };
    
  } catch (error) {
    console.error('[CLEANUP] ❌ Error durante la limpieza:', error);
    throw error;
  } finally {
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

const { createSession, testAPIConnection, initializeExistingSessions, monitorearSesiones, limpiarSesionesHuerfanas } = require('./app/wppconnect');

/**
 * Función para verificar integridad de directorios de sesión
 */
async function verificarIntegridadSesiones() {
  const fs = require('fs');
  const path = require('path');
  // Unificar ruta con wppconnect.js -> tokens está en webconnect-app/tokens
  const tokensDir = path.join(__dirname, '../tokens');
  
  console.log('[INIT] 🔍 Verificando integridad de sesiones...');
  
  if (!fs.existsSync(tokensDir)) {
    console.log('[INIT] 📁 Creando directorio de tokens...');
    fs.mkdirSync(tokensDir, { recursive: true });
    return [];
  }
  
  const sessionDirs = fs.readdirSync(tokensDir)
    .filter(dir => {
      const fullPath = path.join(tokensDir, dir);
      return fs.statSync(fullPath).isDirectory() && dir.startsWith('session_');
    })
    .map(dir => dir.replace('session_', ''));
  
  console.log(`[INIT] 📁 Directorios encontrados: [${sessionDirs.map(id => `session_${id}`).join(', ')}]`);
  
  const sesionesValidas = [];
  const sesionesCorruptas = [];
  
  for (const sessionId of sessionDirs) {
    const sessionDir = path.join(tokensDir, `session_${sessionId}`);
    
    console.log(`[INIT] 🔍 Verificando sesión ${sessionId}...`);
    
    // Verificar si el directorio contiene archivos de sesión
    let tieneArchivosImportantes = false;
    
    try {
      const archivos = fs.readdirSync(sessionDir);
      console.log(`[INIT] 📂 Archivos en session_${sessionId}: [${archivos.join(', ')}]`);
      
      // 🔧 CRITERIOS MÁS FLEXIBLES para detectar sesiones válidas
      
      // 1. Verificar si Default es un directorio con contenido
      const defaultDir = path.join(sessionDir, 'Default');
      if (fs.existsSync(defaultDir) && fs.statSync(defaultDir).isDirectory()) {
        const defaultFiles = fs.readdirSync(defaultDir);
        if (defaultFiles.length > 0) {
          tieneArchivosImportantes = true;
          console.log(`[INIT] ✅ Sesión ${sessionId} tiene directorio Default con ${defaultFiles.length} archivos`);
        }
      }
      
      // 2. Buscar archivos importantes específicos
      const archivosImportantes = archivos.filter(archivo => {
        return archivo === 'Default' || 
               archivo === 'SingletonCookie' ||
               archivo === 'session.json' ||
               archivo.includes('Local Storage') ||
               archivo.includes('Session Storage') ||
               archivo.includes('IndexedDB') ||
               archivo.includes('Web Data') ||
               archivo.includes('Cookies') ||
               archivo.includes('.db') ||
               archivo.includes('Cache');
      });
      
      if (archivosImportantes.length > 0) {
        tieneArchivosImportantes = true;
        console.log(`[INIT] ✅ Sesión ${sessionId} tiene archivos importantes: [${archivosImportantes.join(', ')}]`);
      }
      
      // 3. 🔧 CRITERIO MÁS PERMISIVO: Si el directorio tiene contenido, conservarlo
      if (!tieneArchivosImportantes && archivos.length > 0) {
        // Verificar que no sea solo archivos temporales
        const archivosNoTemporales = archivos.filter(archivo => 
          !archivo.startsWith('.') && 
          !archivo.includes('temp') && 
          !archivo.includes('tmp') &&
          archivo !== 'SingletonLock'
        );
        
        if (archivosNoTemporales.length > 0) {
          tieneArchivosImportantes = true;
          console.log(`[INIT] ⚠️ Sesión ${sessionId} tiene ${archivosNoTemporales.length} archivos no temporales - Considerando como válida`);
          console.log(`[INIT] 📋 Archivos no temporales: [${archivosNoTemporales.join(', ')}]`);
        }
      }
      
      // 4. 🔧 ÚLTIMO RECURSO: Si es reciente (menos de 1 hora), conservar
      if (!tieneArchivosImportantes) {
        const sessionStats = fs.statSync(sessionDir);
        const horaActual = new Date();
        const diferenciaTiempo = horaActual - sessionStats.mtime;
        const unaHoraEnMs = 60 * 60 * 1000;
        
        if (diferenciaTiempo < unaHoraEnMs) {
          tieneArchivosImportantes = true;
          console.log(`[INIT] 🕐 Sesión ${sessionId} es reciente (${Math.round(diferenciaTiempo / 60000)} min) - Conservando`);
        }
      }
      
    } catch (readError) {
      console.error(`[INIT] ❌ Error leyendo directorio session_${sessionId}:`, readError.message);
    }
    
    if (tieneArchivosImportantes) {
      sesionesValidas.push(sessionId);
      console.log(`[INIT] ✅ Sesión ${sessionId} marcada como válida`);
    } else {
      sesionesCorruptas.push(sessionId);
      console.log(`[INIT] ⚠️ Sesión ${sessionId} considerada vacía/corrupta - PERO NO ELIMINANDO automáticamente`);
      console.log(`[INIT] 💡 Para eliminar manualmente: rm -rf tokens/session_${sessionId}`);
      // 🔧 NO ELIMINAR AUTOMÁTICAMENTE - Solo reportar
      // try {
      //   fs.rmSync(sessionDir, { recursive: true, force: true });
      //   console.log(`[INIT] ✅ Directorio session_${sessionId} eliminado`);
      // } catch (error) {
      //   console.error(`[INIT] ❌ Error eliminando sesión ${sessionId}:`, error.message);
      // }
    }
  }
  
  console.log(`[INIT] 📊 Resumen: ${sesionesValidas.length} válidas [${sesionesValidas.join(', ')}], ${sesionesCorruptas.length} eliminadas [${sesionesCorruptas.join(', ')}]`);
  return sesionesValidas;
}

/**
 * Función para obtener tenants con sesiones válidas
 */
async function obtenerTenantsConSesionesValidas() {
  let dbClient = null;
  
  try {
    // Obtener tenants activos de la BD
    dbClient = await pool.connect();
    const result = await dbClient.query('SELECT id FROM tenants');
    const tenantsActivos = result.rows.map(tenant => tenant.id.toString());
    
    // Verificar integridad de sesiones en disco
    const sesionesValidas = await verificarIntegridadSesiones();
    
    // 🔧 NUEVO: Intentar restaurar desde backup si no hay sesiones válidas pero hay tenants activos
    const tenantsConSesionValida = tenantsActivos.filter(tenantId => 
      sesionesValidas.includes(tenantId)
    );
    
    const tenantsSinSesion = tenantsActivos.filter(tenantId => 
      !sesionesValidas.includes(tenantId)
    );
    
    console.log(`[INIT] 📋 Tenants activos en BD: [${tenantsActivos.join(', ')}]`);
    console.log(`[INIT] 💾 Sesiones válidas en disco: [${sesionesValidas.join(', ')}]`);
    console.log(`[INIT] 🔗 Tenants con sesión válida: [${tenantsConSesionValida.join(', ')}]`);
    console.log(`[INIT] ⚠️ Tenants sin sesión: [${tenantsSinSesion.join(', ')}]`);
    
    // 🔧 INTENTAR RESTAURAR DESDE BACKUP para tenants sin sesión
    if (tenantsSinSesion.length > 0) {
      console.log(`[INIT] 🔄 Intentando restaurar ${tenantsSinSesion.length} sesiones desde backup...`);
      
      const { restoreFromBackup } = require('./app/wppconnect');
      
      for (const tenantId of tenantsSinSesion) {
        try {
          console.log(`[INIT] 📂 Intentando restaurar backup para tenant ${tenantId}...`);
          const restored = await restoreFromBackup(tenantId);
          
          if (restored) {
            console.log(`[INIT] ✅ Backup restaurado para tenant ${tenantId}`);
            tenantsConSesionValida.push(tenantId);
          } else {
            console.log(`[INIT] ⚠️ No hay backup disponible para tenant ${tenantId}`);
          }
        } catch (restoreError) {
          console.error(`[INIT] ❌ Error restaurando backup para ${tenantId}:`, restoreError.message);
        }
      }
      
      console.log(`[INIT] 📊 Después de restaurar backups - Tenants con sesión: [${tenantsConSesionValida.join(', ')}]`);
    }
    
    return tenantsConSesionValida;
    
  } catch (error) {
    console.error('[INIT] ❌ Error obteniendo tenants con sesiones válidas:', error);
    return [];
  } finally {
    if (dbClient) {
      dbClient.release();
    }
  }
}

// Función de inicialización MEJORADA
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
    
    // 3. Ejecutar limpieza inicial de datos obsoletos
    console.log('[INIT] 🧹 Ejecutando limpieza inicial...');
    await limpiarDatosObsoletos();
    
    // 4. 🔧 NUEVO: Obtener solo tenants con sesiones válidas
    console.log('[INIT] 🔍 Verificando tenants con sesiones válidas...');
    const tenantsConSesionValida = await obtenerTenantsConSesionesValidas();
    
    if (tenantsConSesionValida.length > 0) {
      // 5. Restaurar SOLO sesiones válidas
      console.log('[INIT] 📱 Restaurando sesiones válidas...');
      await initializeExistingSessions(tenantsConSesionValida);
    } else {
      console.log('[INIT] ℹ️ No hay sesiones válidas para restaurar');
    }
    
    // 6. ✨ Limpiar sesiones huérfanas después de la inicialización
    console.log('[INIT] 🗑️ Limpiando sesiones huérfanas...');
    await limpiarSesionesHuerfanas();
    
    // 7. Programar limpieza periódica
    programarLimpiezaPeriodica();
    
    // 8. ✨ Iniciar monitoreo de sesiones
    console.log('[INIT] 🔍 Iniciando monitoreo de sesiones...');
    monitorearSesiones();
    
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
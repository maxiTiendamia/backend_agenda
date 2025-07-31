require('dotenv').config();
const express = require('express');
const router = express.Router();
const { pool } = require('../app/database'); // ✅ Usar importación consistente
const redis = require('../app/redisClient'); // ✅ Usar redisClient unificado
const { createSession } = require('../app/wppconnect');
const { guardarQR, limpiarQR } = require('../app/qrUtils');
const { getSessionFolder, ensureSessionFolder, limpiarSingletonLock, getSession } = require('../app/sessionUtils');
const fs = require('fs');
const path = require('path');
const axios = require('axios');

// NUEVA FUNCIÓN: Enviar mensaje por WhatsApp usando la sesión correspondiente
async function sendMessageToClient(sessionId, telefono, mensaje) {
  const session = getSession(String(sessionId));
  if (!session) throw new Error(`No existe sesión activa para el cliente ${sessionId}`);
  // Asegúrate de que el número esté en formato internacional y termine con @c.us
  const chatId = `${telefono}@c.us`;
  await session.sendMessage(chatId, mensaje);
}

// Endpoint para obtener el estado de todas las sesiones (MEJORADO)
router.get('/estado-sesiones', async (req, res) => {
  try {
    const result = await pool.query('SELECT id, qr_code FROM tenants');
    const sesiones = [];

    for (const row of result.rows) {
      const clienteId = row.id;
      const sessionFolder = getSessionFolder(String(clienteId));
      
      // Verificar archivos de sesión
      const tieneArchivos = fs.existsSync(sessionFolder) && fs.readdirSync(sessionFolder).length > 0;
      
      // Verificar si hay sesión activa en memoria
      const sesionEnMemoria = getSession(String(clienteId)) ? true : false;
      
      // Verificar datos en Redis
      let datosEnRedis = false;
      try {
        const redisKeys = await redis.keys(`*${clienteId}*`);
        datosEnRedis = redisKeys.length > 0;
      } catch (redisError) {
        console.log(`[ESTADO] Error verificando Redis para cliente ${clienteId}:`, redisError.message);
      }

      // Determinar estado basado en múltiples factores
      let estado = 'NO_INICIADA';
      let detalles = {
        enMemoria: sesionEnMemoria,
        tieneArchivos: tieneArchivos,
        tieneQR: !!row.qr_code,
        datosEnRedis: datosEnRedis
      };

      // Lógica de estado más precisa
      if (sesionEnMemoria && tieneArchivos) {
        // Si hay sesión en memoria Y archivos, está realmente conectado
        estado = 'CONNECTED';
      } else if (tieneArchivos && !sesionEnMemoria) {
        // Hay archivos pero no sesión activa - necesita reconexión
        estado = 'ARCHIVOS_DISPONIBLES';
      } else if (row.qr_code && !tieneArchivos) {
        // Hay QR pero no archivos - esperando escaneo
        estado = 'QR_GENERATED';
      } else if (!row.qr_code && !tieneArchivos) {
        // No hay nada - no iniciada
        estado = 'NO_INICIADA';
      } else {
        // Estados de error
        if (sesionEnMemoria && !tieneArchivos) {
          estado = 'ERROR'; // Sesión en memoria pero sin archivos
        } else {
          estado = 'UNPAIRED'; // Otros estados problemáticos
        }
      }

      // Verificación adicional del estado real de la sesión
      if (sesionEnMemoria) {
        try {
          const session = getSession(String(clienteId));
          if (session) {
            // Verificar si la sesión está realmente conectada
            const isConnected = await session.getConnectionState();
            if (isConnected === 'CONNECTED') {
              estado = 'CONNECTED';
            } else if (isConnected === 'TIMEOUT') {
              estado = 'TIMEOUT';
            } else if (isConnected === 'DISCONNECTED') {
              estado = 'DISCONNECTED';
            }
          }
        } catch (sessionError) {
          estado = 'ERROR';
          detalles.error = sessionError.message;
        }
      }

      sesiones.push({
        clienteId: clienteId,
        estado: estado,
        ...detalles,
        timestamp: new Date().toISOString()
      });
    }

    res.json(sesiones);
  } catch (err) {
    console.error('[ESTADO] Error obteniendo estado de sesiones:', err);
    res.status(500).json({ error: err.message });
  }
});

// Endpoint para regenerar QR manualmente
router.post('/generar-qr/:sessionId', async (req, res) => {
  const { sessionId } = req.params;
  try {
    console.log(`[WEBCONNECT] Reinicio manual de QR para cliente ${sessionId}`);
    
    // 🔥 PASO 1: Cerrar sesión existente si está activa
    const { sessions, clearSession } = require('../app/wppconnect');
    if (sessions[sessionId]) {
      console.log(`[WEBCONNECT] 🔄 Cerrando sesión existente para ${sessionId}...`);
      try {
        // Cerrar la sesión
        await sessions[sessionId].close();
        console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} cerrada correctamente`);
      } catch (closeError) {
        console.error(`[WEBCONNECT] ⚠️ Error cerrando sesión ${sessionId}:`, closeError.message);
      }
      
      // Eliminar de memoria
      delete sessions[sessionId];
      console.log(`[WEBCONNECT] 🗑️ Sesión ${sessionId} eliminada de memoria`);
    }
    
    // 🔥 PASO 2: Limpiar archivos y locks
    await limpiarQR(pool, sessionId);
    await limpiarSingletonLock(sessionId);
    
    // 🔥 PASO 3: Esperar un poco para que se liberen los recursos
    await new Promise(resolve => setTimeout(resolve, 2000));
    
    // 🔥 PASO 4: Limpiar directorio de tokens completamente
    const sessionFolder = getSessionFolder(sessionId);
    if (fs.existsSync(sessionFolder)) {
      try {
        // Eliminar todo el directorio
        fs.rmSync(sessionFolder, { recursive: true, force: true });
        console.log(`[WEBCONNECT] 🗑️ Directorio ${sessionFolder} eliminado completamente`);
        
        // Recrear directorio vacío
        await ensureSessionFolder(sessionId);
        console.log(`[WEBCONNECT] 📁 Directorio ${sessionFolder} recreado`);
      } catch (dirError) {
        console.error(`[WEBCONNECT] Error manejando directorio:`, dirError.message);
      }
    }
    
    // 🔥 PASO 5: Crear nueva sesión
    console.log(`[WEBCONNECT] 🚀 Creando nueva sesión para ${sessionId}...`);
    await createSession(sessionId, async (qr) => {
      console.log(`[WEBCONNECT] QR generado para cliente ${sessionId} (manual)`);
      await guardarQR(pool, sessionId, qr, true);
      console.log(`[WEBCONNECT] QR guardado en base de datos para cliente ${sessionId}`);
    });
    
    res.json({ 
      ok: true, 
      message: `QR regenerado exitosamente para cliente ${sessionId}`,
      timestamp: new Date().toISOString()
    });
    
  } catch (err) {
    console.error(`[WEBCONNECT][ERROR] Error al regenerar QR para ${sessionId}:`, err);
    res.status(500).json({ 
      ok: false, 
      error: err.message,
      details: 'Error durante regeneración de QR'
    });
  }
});

// Endpoint para debug de errores de sesión (mock básico)
router.get('/debug/errores', async (req, res) => {
  res.json({ session_errors: {} });
});

// Endpoint para restaurar sesiones desde Redis al reiniciar el VPS
router.post('/restore-sessions', async (req, res) => {
  try {
    const keys = await redis.keys('session:*');
    let restauradas = 0;
    for (const key of keys) {
      const sessionData = await redis.get(key);
      if (sessionData) {
        const sessionId = key.replace('session:', '');
        // Restaurar archivos de sesión en disco
        const folder = getSessionFolder(sessionId);
        if (!fs.existsSync(folder)) fs.mkdirSync(folder, { recursive: true });
        const files = JSON.parse(sessionData);
        for (const file of files) {
          const filePath = path.join(folder, file.name);
          fs.writeFileSync(filePath, Buffer.from(file.data, 'base64'));
        }
        // Intentar reconectar sesión
        await createSession(sessionId);
        restauradas++;
      }
    }
    res.json({ ok: true, restauradas });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// Endpoint mejorado para restart-qr
router.post('/restart-qr/:sessionId', async (req, res) => {
  const { sessionId } = req.params;
  
  // Validar sessionId
  if (!sessionId || isNaN(sessionId)) {
    return res.status(400).json({ 
      ok: false, 
      error: 'ID de sesión inválido' 
    });
  }
  
  try {
    console.log(`[WEBCONNECT] Reinicio manual de QR para cliente ${sessionId}`);
    
    // Verificar que el cliente existe en la BD antes de proceder
    const { verificarClienteExisteEnBD } = require('../app/wppconnect');
    const clienteExiste = await verificarClienteExisteEnBD(sessionId);
    
    if (!clienteExiste) {
      return res.status(404).json({
        ok: false,
        error: `Cliente ${sessionId} no encontrado en la base de datos`
      });
    }

    // 🔥 PASO 1: Cerrar sesión existente si está activa
    const { sessions, clearSession } = require('../app/wppconnect');
    if (sessions[sessionId]) {
      console.log(`[WEBCONNECT] 🔄 Cerrando sesión existente para ${sessionId}...`);
      try {
        // Cerrar la sesión
        await sessions[sessionId].close();
        console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} cerrada correctamente`);
      } catch (closeError) {
        console.error(`[WEBCONNECT] ⚠️ Error cerrando sesión ${sessionId}:`, closeError.message);
      }
      
      // Eliminar de memoria
      delete sessions[sessionId];
      console.log(`[WEBCONNECT] 🗑️ Sesión ${sessionId} eliminada de memoria`);
    }
    
    // 🔥 PASO 2: Limpiar archivos y locks
    await limpiarQR(pool, sessionId);
    await limpiarSingletonLock(sessionId);
    
    // 🔥 PASO 3: Esperar un poco para que se liberen los recursos
    await new Promise(resolve => setTimeout(resolve, 2000));
    
    // 🔥 PASO 4: Limpiar directorio de tokens completamente
    const sessionFolder = getSessionFolder(sessionId);
    if (fs.existsSync(sessionFolder)) {
      try {
        // Eliminar todo el directorio
        fs.rmSync(sessionFolder, { recursive: true, force: true });
        console.log(`[WEBCONNECT] 🗑️ Directorio ${sessionFolder} eliminado completamente`);
        
        // Recrear directorio vacío
        await ensureSessionFolder(sessionId);
        console.log(`[WEBCONNECT] 📁 Directorio ${sessionFolder} recreado`);
      } catch (dirError) {
        console.error(`[WEBCONNECT] Error manejando directorio:`, dirError.message);
      }
    }
    
    // 🔥 PASO 5: Crear nueva sesión
    console.log(`[WEBCONNECT] 🚀 Creando nueva sesión para ${sessionId}...`);
    await createSession(sessionId, async (qr) => {
      console.log(`[WEBCONNECT] QR generado para cliente ${sessionId} (manual)`);
      await guardarQR(pool, sessionId, qr, true);
      console.log(`[WEBCONNECT] QR guardado en base de datos para cliente ${sessionId}`);
    });
    
    res.json({ 
      ok: true, 
      message: `QR reiniciado exitosamente para cliente ${sessionId}`,
      timestamp: new Date().toISOString()
    });
    
  } catch (err) {
    console.error(`[WEBCONNECT][ERROR] Error al reiniciar QR para ${sessionId}:`, err);
    res.status(500).json({ 
      ok: false, 
      error: err.message,
      details: 'Error durante reinicio de QR'
    });
  }
});

// Middleware para guardar archivos de sesión en Redis al conectarse un cliente
async function saveSessionToRedis(sessionId) {
  try {
    const fs = require('fs');
    const path = require('path');
    
    const sessionFolder = path.join(__dirname, '../../tokens', `session_${sessionId}`);
    
    // Verificar que la carpeta existe
    if (!fs.existsSync(sessionFolder)) {
      console.log(`[REDIS] No hay carpeta de tokens para sesión ${sessionId}`);
      return;
    }

    // Obtener lista de archivos (NO directorios)
    const files = fs.readdirSync(sessionFolder).filter(file => {
      const filePath = path.join(sessionFolder, file);
      const stats = fs.statSync(filePath);
      return stats.isFile(); // Solo archivos, no directorios
    });

    if (files.length === 0) {
      console.log(`[REDIS] No hay archivos de tokens para sesión ${sessionId}`);
      return;
    }

    console.log(`[REDIS] Guardando ${files.length} archivos de sesión ${sessionId} en Redis...`);

    // Guardar cada archivo en Redis
    for (const file of files) {
      try {
        const filePath = path.join(sessionFolder, file);
        
        // Verificar nuevamente que es un archivo
        const stats = fs.statSync(filePath);
        if (!stats.isFile()) {
          console.log(`[REDIS] Saltando directorio: ${file}`);
          continue;
        }

        // Leer el contenido del archivo
        const content = fs.readFileSync(filePath, 'utf8');
        
        // Guardar en Redis con una clave única
        const redisKey = `session_${sessionId}_file_${file}`;
        await redis.set(redisKey, content, 'EX', 3600); // Expira en 1 hora
        
        console.log(`[REDIS] ✅ Archivo guardado: ${file} -> ${redisKey}`);
        
      } catch (fileError) {
        console.error(`[REDIS] Error procesando archivo ${file}:`, fileError.message);
      }
    }

    // Guardar metadatos de la sesión
    const sessionData = {
      sessionId: sessionId,
      filesCount: files.length,
      timestamp: new Date().toISOString(),
      status: 'active'
    };

    await redis.set(`session_${sessionId}_metadata`, JSON.stringify(sessionData), 'EX', 3600);
    console.log(`[REDIS] ✅ Metadatos de sesión ${sessionId} guardados en Redis`);

  } catch (error) {
    console.error(`[REDIS] Error guardando sesión ${sessionId} en Redis:`, error.message);
  }
}

// Ejemplo de uso: al crear sesión, guardar archivos en Redis
router.post('/iniciar/:sessionId', async (req, res) => {
  const { sessionId } = req.params;
  console.log(`[WEBCONNECT] Solicitud de inicio de sesión para cliente ${sessionId}`);
  try {
    await ensureSessionFolder(sessionId);
    await createSession(sessionId, async (qr) => {
      console.log(`[WEBCONNECT] QR generado para cliente ${sessionId}`);
      await guardarQR(pool, sessionId, qr, true);
    });
    await saveSessionToRedis(sessionId);
    console.log(`[WEBCONNECT] Sesión ${sessionId} creada y guardada en Redis`);
    res.json({ ok: true, message: 'Sesión creada y guardada en Redis' });
  } catch (err) {
    console.error(`[WEBCONNECT][ERROR] Error al crear sesión para ${sessionId}:`, err);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// Obtener el QR actual de un cliente
router.get('/qr/:sessionId', async (req, res) => {
  const { sessionId } = req.params;
  try {
    const result = await pool.query('SELECT qr_code FROM tenants WHERE id = $1', [sessionId]);
    if (result.rows.length > 0 && result.rows[0].qr_code) {
      res.json({ ok: true, qr: result.rows[0].qr_code });
    } else {
      res.json({ ok: false, message: 'QR no disponible aún' });
    }
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// Recibe mensajes de WhatsApp y los reenvía a la API y reenvía respuesta al usuario
router.post('/webhook', async (req, res) => {
  const { sessionId, telefono, mensaje } = req.body;
  try {
    // Reenviar a la API
    const apiRes = await axios.post('http://localhost:8000/api/webhook', {
      cliente_id: sessionId,
      telefono,
      mensaje
    });
    // Enviar la respuesta de la API al cliente por WhatsApp usando la sesión correspondiente
    if (apiRes.data && apiRes.data.mensaje && apiRes.data.mensaje.trim() !== "") {
      await sendMessageToClient(sessionId, telefono, apiRes.data.mensaje);
      console.log(`[WEBCONNECT] Mensaje enviado a ${telefono}: ${apiRes.data.mensaje}`);
    } else {
      console.log(`[WEBCONNECT] No hay mensaje para enviar a ${telefono}`);
    }
    res.json({ ok: true });
  } catch (err) {
    console.error(`[WEBCONNECT][ERROR] Al procesar webhook:`, err);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// Endpoint para obtener estado detallado de una sesión específica
router.get('/estado-sesion/:sessionId', async (req, res) => {
  const { sessionId } = req.params;
  
  try {
    // Obtener datos del cliente
    const result = await pool.query('SELECT id, nombre, comercio, qr_code FROM tenants WHERE id = $1', [sessionId]);
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Cliente no encontrado' });
    }
    
    const cliente = result.rows[0];
    const sessionFolder = getSessionFolder(String(sessionId));
    
    // Verificaciones detalladas
    const tieneArchivos = fs.existsSync(sessionFolder) && fs.readdirSync(sessionFolder).length > 0;
    const sesionEnMemoria = getSession(String(sessionId)) ? true : false;
    
    // Listar archivos en el directorio de sesión
    let archivos = [];
    if (fs.existsSync(sessionFolder)) {
      archivos = fs.readdirSync(sessionFolder).map(file => {
        const filePath = path.join(sessionFolder, file);
        const stats = fs.statSync(filePath);
        return {
          nombre: file,
          tamaño: stats.size,
          fechaModificacion: stats.mtime
        };
      });
    }
    
    

    // Verificar datos en Redis
    let datosRedis = [];
    try {
      const redisKeys = await redis.keys(`*${sessionId}*`);
      for (const key of redisKeys) {
        const tipo = await redis.type(key);
        const ttl = await redis.ttl(key);
        datosRedis.push({
          clave: key,
          tipo: tipo,
          ttl: ttl === -1 ? 'Sin expiración' : `${ttl}s`
        });
      }
    } catch (redisError) {
      datosRedis = [{ error: redisError.message }];
    }

    // Estado de la sesión
    let estadoSesion = 'DISCONNECTED';
    let infoSesion = null;
    
    if (sesionEnMemoria) {
      try {
        const session = getSession(String(sessionId));
        if (session) {
          // Obtener información detallada de la sesión
          estadoSesion = await session.getConnectionState() || 'CONNECTED';
          infoSesion = {
            isConnected: await session.isConnected(),
            batteryLevel: await session.getBatteryLevel().catch(() => null),
            hostDevice: await session.getHostDevice().catch(() => null)
          };
        }
      } catch (sessionError) {
        estadoSesion = 'ERROR';
        infoSesion = { error: sessionError.message };
      }
    }

    // Determinar estado general
    let estadoGeneral = 'NO_INICIADA';
    if (estadoSesion === 'CONNECTED' && tieneArchivos) {
      estadoGeneral = 'CONNECTED';
    } else if (tieneArchivos && estadoSesion !== 'CONNECTED') {
      estadoGeneral = 'ARCHIVOS_DISPONIBLES';
    } else if (cliente.qr_code && !tieneArchivos) {
      estadoGeneral = 'QR_GENERATED';
    } else if (estadoSesion === 'ERROR') {
      estadoGeneral = 'ERROR';
    } else if (estadoSesion === 'TIMEOUT') {
      estadoGeneral = 'TIMEOUT';
    } else if (estadoSesion === 'DISCONNECTED' && tieneArchivos) {
      estadoGeneral = 'DISCONNECTED';
    }

    res.json({
      cliente: {
        id: cliente.id,
        nombre: cliente.nombre,
        comercio: cliente.comercio,
        tieneQR: !!cliente.qr_code
      },
      estado: estadoGeneral,
      estadoDetallado: {
        sesionEnMemoria: sesionEnMemoria,
        estadoSesion: estadoSesion,
        tieneArchivos: tieneArchivos,
        cantidadArchivos: archivos.length,
        datosEnRedis: datosRedis.length > 0
      },
      archivos: archivos,
      datosRedis: datosRedis,
      infoSesion: infoSesion,
      timestamp: new Date().toISOString()
    });

  } catch (err) {
    console.error(`[ESTADO] Error obteniendo estado detallado para ${sessionId}:`, err);
    res.status(500).json({ error: err.message });
  }
});

// Endpoint para verificar conectividad real de una sesión
router.get('/verificar-conexion/:sessionId', async (req, res) => {
  const { sessionId } = req.params;
  
  try {
    const session = getSession(String(sessionId));
    if (!session) {
      return res.json({ 
        conectado: false, 
        error: 'Sesión no encontrada en memoria' 
      });
    }

    // Intentar obtener estado de la sesión
    const isConnected = await session.isConnected();
    const connectionState = await session.getConnectionState();
    
    // Intentar una operación simple para verificar conectividad
    let puedeEnviarMensajes = false;
    try {
      await session.getChats();
      puedeEnviarMensajes = true;
    } catch (e) {
      puedeEnviarMensajes = false;
    }

    res.json({
      conectado: isConnected,
      estado: connectionState,
      puedeEnviarMensajes: puedeEnviarMensajes,
      timestamp: new Date().toISOString()
    });

  } catch (error) {
    res.json({
      conectado: false,
      error: error.message,
      timestamp: new Date().toISOString()
    });
  }
});

// NUEVA RUTA: Limpiar sesiones huérfanas
router.post('/limpiar-sesiones-huerfanas', async (req, res) => {
  try {
    const { limpiarSesionesHuerfanas } = require('../app/wppconnect');
    
    console.log('[WEBCONNECT] 🧹 Solicitud manual de limpieza de sesiones huérfanas...');
    const sesionesLimpiadas = await limpiarSesionesHuerfanas();
    
    res.json({
      success: true,
      message: `Limpieza completada. ${sesionesLimpiadas} sesiones huérfanas eliminadas.`,
      sesionesLimpiadas
    });
  } catch (error) {
    console.error('[WEBCONNECT] Error en limpieza manual:', error);
    res.status(500).json({
      success: false,
      error: 'Error durante la limpieza de sesiones huérfanas',
      details: error.message
    });
  }
});

// NUEVA RUTA: Verificar si un cliente existe en BD
router.get('/verificar-cliente/:sessionId', async (req, res) => {
  try {
    const { sessionId } = req.params;
    const { verificarClienteExisteEnBD } = require('../app/wppconnect');
    
    const existe = await verificarClienteExisteEnBD(sessionId);
    
    res.json({
      sessionId,
      existe,
      timestamp: new Date().toISOString()
    });
  } catch (error) {
    res.status(500).json({
      error: error.message,
      timestamp: new Date().toISOString()
    });
  }
});

// Endpoint de salud mejorado
router.get('/health-detailed', async (req, res) => {
  try {
    const redis = require('../app/redisClient');
    const { pool } = require('../app/database');
    const { sessions } = require('../app/wppconnect');
    
    // Verificar Redis
    let redisStatus = 'error';
    try {
      await redis.ping();
      redisStatus = 'ok';
    } catch (redisError) {
      console.error('[HEALTH] Redis error:', redisError);
    }
    
    // Verificar PostgreSQL
    let dbStatus = 'error';
    let dbClient = null;
    try {
      dbClient = await pool.connect();
      await dbClient.query('SELECT 1');
      dbStatus = 'ok';
    } catch (dbError) {
      console.error('[HEALTH] DB error:', dbError);
    } finally {
      if (dbClient) dbClient.release();
    }
    
    // Contar sesiones activas
    const sesionesActivas = Object.keys(sessions).length;
    
    res.json({
      status: 'ok',
      timestamp: new Date().toISOString(),
      services: {
        redis: redisStatus,
        database: dbStatus,
        sessions: {
          active: sesionesActivas,
          list: Object.keys(sessions)
        }
      },
      uptime: process.uptime(),
      memory: process.memoryUsage()
    });
    
  } catch (error) {
    res.status(500).json({
      status: 'error',
      error: error.message,
      timestamp: new Date().toISOString()
    });
  }
});

module.exports = router;
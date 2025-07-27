require('dotenv').config();
const express = require('express');
const { pool } = require('./db');
const axios = require('axios');


// Configuración de reconexión automática para Redis
const { createClient } = require('redis');
const redisClient = createClient({
  url: process.env.REDIS_URL,
  socket: {
    reconnectStrategy: (retries) => {
      // Espera exponencial hasta 30s
      return Math.min(retries * 100, 30000);
    }
  }
});
redisClient.on('error', (err) => {
  console.error('❌ Redis error:', err);
});
redisClient.on('reconnecting', () => {
  console.warn('🔄 Reintentando conexión a Redis...');
});
redisClient.connect().catch((err) => {
  console.error('❌ Error conectando a Redis:', err);
});
const { createSession, getLoggedSessions, getSessionsWithInfo, reconnectSessionsWithInfo } = require('./wppconnect');
const fs = require('fs');
const path = require('path');
const app = express();
const PORT = process.env.PORT || 3000;
app.use(express.json());



const sessions = {};
const sessionErrors = {};
// Lock para evitar restauraciones simultáneas
const restaurandoSesiones = {};

// Guardar QR en base de datos
const { guardarQR } = require('./qrUtils');

// Limpiar archivo SingletonLock si existe
async function limpiarSingletonLock(sessionId) {
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
  const singletonLockPath = path.join(sessionDir, sessionId, "SingletonLock");
  if (fs.existsSync(singletonLockPath)) {
    try {
      fs.unlinkSync(singletonLockPath);
      console.log(`🔓 SingletonLock eliminado en ${singletonLockPath} para cliente ${sessionId}`);
    } catch (err) {
      console.error(`❌ Error eliminando SingletonLock en ${singletonLockPath} para ${sessionId}:`, err.message);
    }
  }
}

// Crear sesión y manejar QR/mensajes
async function crearSesionWPP(sessionId, permitirGuardarQR = true) {
  // Lock para evitar restauraciones simultáneas
  if (restaurandoSesiones[sessionId]) {
    console.log(`[LOCK] Ya se está restaurando la sesión ${sessionId}, pero se fuerza la creación/restauración para obtener QR.`);
    // No retornes, sigue el flujo para que se genere el QR aunque esté restaurando
  }
  restaurandoSesiones[sessionId] = true;
  try {
    await limpiarSingletonLock(sessionId);
    if (sessions[sessionId]) return sessions[sessionId];
    const client = await createSession(
      sessionId,
      async (base64Qr) => {
        if (permitirGuardarQR) {
          await guardarQR(pool, sessionId, base64Qr);
        }
      },
      async (message, client) => {
        try {
          const telefono = message.from.replace("@c.us", "");
          const mensaje = message.body;
          const cliente_id = sessionId;
          const backendResponse = await axios.post(
            "https://backend-agenda-2.onrender.com/api/webhook",
            { telefono, mensaje, cliente_id }
          );
          const respuesta = backendResponse.data && backendResponse.data.mensaje;
          if (respuesta) {
            await client.sendText(`${telefono}@c.us`, respuesta);
          }
        } catch (err) {
          console.error("Error reenviando mensaje a backend o enviando respuesta:", err);
        }
      }
    );
    sessions[sessionId] = client;
    return client;
  } finally {
    delete restaurandoSesiones[sessionId];
  }
}

// Restaurar sesiones desde Redis (para todas las que tienen info previa)
async function restaurarSesiones() {
  const sessionsWithInfo = await getSessionsWithInfo();
  for (const sessionId of sessionsWithInfo) {
    try {
      await crearSesionWPP(sessionId, false);
      console.log(`Restaurando sesión para cliente con info previa ${sessionId} (solo Redis)`);
    } catch (err) {
      console.error(`Error restaurando sesión ${sessionId}:`, err.message);
    }
  }
}

// Endpoints

// Endpoint para ver todas las claves y valores de Redis de una sesión específica
app.get('/debug/redis/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  try {
    const keys = await redisClient.keys(`wppconnect:${clienteId}:*`);
    const datos = {};
    for (const key of keys) {
      datos[key] = await redisClient.get(key);
    }
    res.json({ claves: keys, datos });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/qr/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  try {
    const result = await pool.query('SELECT qr_code FROM tenants WHERE id = $1', [clienteId]);
    if (result.rows.length && result.rows[0].qr_code) {
      const qrCodeData = result.rows[0].qr_code;
      const html = `<html><body style="display:flex;justify-content:center;align-items:center;height:100vh;"><img src="data:image/png;base64,${qrCodeData}" /></body></html>`;
      res.send(html);
    } else {
      res.status(404).send('QR no encontrado');
    }
  } catch (err) {
    res.status(500).send('Error al buscar QR');
  }
});

app.post('/generar-qr/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  try {
    await crearSesionWPP(clienteId, true);
    res.json({ ok: true, mensaje: `QR regenerado para cliente ${clienteId}` });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.get('/sesiones', async (req, res) => {
  const result = await pool.query('SELECT id, comercio FROM tenants ORDER BY id');
  const clientes = result.rows.map(row => String(row.id));
  const estados = await Promise.all(clientes.map(async id => {
    let estado = 'desconocido';
    if (sessions[id] && sessions[id].isConnected) {
      try {
        estado = (await sessions[id].isConnected()) ? 'conectada' : 'desconectada';
      } catch {
        estado = 'error';
      }
    } else {
      estado = 'sin_sesion';
    }
    return { id, estado };
  }));
  res.json(estados);
});

app.post('/reiniciar/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  try {
    // 1. Limpiar claves de Redis
    const keys = await redisClient.keys(`wppconnect:${clienteId}:*`);
    for (const key of keys) {
      await redisClient.del(key);
    }
    // 2. Limpiar archivos locales (tokens, SingletonLock)
    const fs = require('fs');
    const path = require('path');
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
    const dirPath = path.join(sessionDir, String(clienteId));
    if (fs.existsSync(dirPath)) {
      fs.rmSync(dirPath, { recursive: true, force: true });
    }
    // 3. Limpiar QR en base de datos
    await pool.query('UPDATE tenants SET qr_code = NULL WHERE id = $1', [clienteId]);
    // 4. Forzar generación de nuevo QR (esto también lo guarda en la base)
    await crearSesionWPP(clienteId, true);
    res.json({ ok: true, mensaje: `Sesión completamente reiniciada y QR regenerado para cliente ${clienteId}` });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.post('/reset-errores/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  sessionErrors[clienteId] = 0;
  res.json({ ok: true, mensaje: `Errores reseteados para cliente ${clienteId}` });
});

app.get('/debug/errores', (req, res) => {
  res.json({ sessionErrors, sesionesEnMemoria: Object.keys(sessions), timestamp: new Date().toISOString() });
});

app.get('/debug/redis', async (req, res) => {
  try {
    const keys = await redisClient.keys('wppconnect:*');
    const datos = {};
    for (const key of keys) {
      datos[key] = await redisClient.get(key);
    }
    res.json({ claves: keys, datos });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/healthz', async (req, res) => {
  try {
    // Verifica conexión a Redis y PostgreSQL
    await redisClient.ping();
    await pool.query('SELECT 1');
    res.status(200).json({ status: 'ok' });
  } catch (err) {
    res.status(500).json({ status: 'error', error: err.message });
  }
});

app.get('/estado-sesiones', async (req, res) => {
  try {
    const result = await pool.query('SELECT id, nombre, comercio FROM tenants ORDER BY id');
    const clientes = result.rows;
    const estados = await Promise.all(
      clientes.map(async (row) => {
        const id = String(row.id);
        let estado = 'NO_INICIADA';
        let enMemoria = false;
        if (sessions[id] && sessions[id].isConnected) {
          try {
            estado = (await sessions[id].isConnected()) ? 'CONNECTED' : 'DISCONNECTED';
            enMemoria = true;
          } catch {
            estado = 'ERROR';
          }
        }
        // Ya no se verifica archivos locales
        return {
          clienteId: id,
          nombre: row.nombre,
          comercio: row.comercio,
          estado,
          enMemoria
        };
      })
    );
    res.json(estados);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});


const { cleanInvalidSessions } = require('./wppconnect');

// Inicializar la aplicación: limpiar sesiones inválidas y restaurar sesiones previas
async function inicializarAplicacion() {
  try {
    (async () => {
      try {
        await redisClient.ping();
        console.log('✅ Conexión a Redis exitosa');
        const keys = await redisClient.keys('wppconnect:*');
        console.log(`🔑 Claves encontradas en Redis: ${keys.length}`);
      } catch (err) {
        console.error('❌ Error conectando a Redis:', err);
        process.exit(1);
      }
    })();

    // Limpiar sesiones inválidas antes de restaurar
    await cleanInvalidSessions();
    await restaurarSesiones();
    console.log('🚀 Inicialización completa');
  } catch (err) {
    console.error('Error durante la inicialización:', err);
  }
}

const server = app.listen(PORT).on('listening', async () => {
  console.log(`✅ WPPConnect-service corriendo en puerto ${PORT}`);
  await inicializarAplicacion();
  // Restaurar sesiones logueadas desde Redis
  const sesionesLogueadas = await getLoggedSessions();
  for (const sessionId of sesionesLogueadas) {
    try {
      await crearSesionWPP(sessionId, false);
      console.log(`[INIT] Sesión logueada restaurada: ${sessionId}`);
    } catch (err) {
      console.error(`[INIT] Error restaurando sesión logueada ${sessionId}:`, err);
    }
  }
}).on('error', (error) => {
  if (error.code === 'EADDRINUSE') {
    console.error(`Puerto ${PORT} ya está en uso. Intentando puerto alternativo...`);
    const server2 = app.listen(0).on('listening', async () => {
      const actualPort = server2.address().port;
      console.log(`✅ WPPConnect-service corriendo en puerto alternativo ${actualPort}`);
      await inicializarAplicacion();
    }).on('error', (err) => {
      console.error('Error fatal iniciando servidor:', err);
      process.exit(1);
    });
  } else {
    console.error('Error iniciando servidor:', error);
    process.exit(1);
  }
});

app.get('/debug/desconexiones/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  const data = await redisClient.get(`wppconnect:${clienteId}:lastDisconnect`);
  res.json(data ? JSON.parse(data) : { mensaje: 'Sin desconexiones registradas' });
});

module.exports = { pool };

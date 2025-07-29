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
const { createSession, getLoggedSessions, getSessionsWithInfo, reconnectSessionsWithInfo, reconnectLoggedSessions } = require('./wppconnect');
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
const { ensureSessionFolder, getSessionFolder } = require('./sessionUtils');

// Limpia todos los SingletonLock dentro de la carpeta de sesión (incluyendo subcarpetas)
async function limpiarSingletonLock(sessionId) {
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
  const basePath = path.join(sessionDir, sessionId);
  if (!fs.existsSync(basePath)) return;
  // Busca recursivamente todos los archivos SingletonLock
  function buscarYEliminar(dir) {
    let files;
    try {
      files = fs.readdirSync(dir);
    } catch (err) {
      // Si la carpeta no existe, salir silenciosamente
      return;
    }
    for (const file of files) {
      const fullPath = path.join(dir, file);
      if (!fs.existsSync(fullPath)) continue; // <-- Agrega este chequeo
      if (fs.statSync(fullPath).isDirectory()) {
        buscarYEliminar(fullPath);
      } else if (file === "SingletonLock") {
        try {
          fs.unlinkSync(fullPath);
          console.log(`🔓 SingletonLock eliminado en ${fullPath} para cliente ${sessionId}`);
        } catch (err) {
          console.error(`❌ Error eliminando SingletonLock en ${fullPath} para ${sessionId}:`, err.message);
        }
      }
    }
  }
  buscarYEliminar(basePath);
}

// Crear sesión y manejar QR/mensajes
const sesionesEnProceso = new Set();

async function crearSesionWPP(sessionId, permitirGuardarQR = true) {
  if (sesionesEnProceso.has(sessionId)) {
    throw new Error(`⚠️ Sesión ${sessionId} ya está en proceso.`);
  }
  sesionesEnProceso.add(sessionId);

  const redisKey = `session:lock:${sessionId}`;
  const isLocked = await redisClient.get(redisKey);
  if (isLocked) {
    sesionesEnProceso.delete(sessionId);
    throw new Error("⚠️ Sesión ya está siendo inicializada (lock Redis).");
  }
  await redisClient.set(redisKey, "1", 'EX', 30); // Lock 30 segundos

  try {
    await ensureSessionFolder(sessionId);
    await limpiarSingletonLock(sessionId);

    const sessionPath = getSessionFolder(sessionId);

    const create = async () => {
      return await createSession(
        sessionId,
        async (base64Qr) => {
          if (permitirGuardarQR) {
            await guardarQR(pool, sessionId, base64Qr);
          }
        },
        async (message, client) => {
          console.log(`[BOT ${sessionId}] Mensaje recibido:`, message);
          try {
            const telefono = message.from.replace("@c.us", "");
            const mensaje = message.body;
            const cliente_id = sessionId;

            const result = await pool.query(
              'SELECT 1 FROM blocked_numbers WHERE cliente_id = $1 AND telefono = $2 LIMIT 1',
              [cliente_id, telefono]
            );
            if (result.rows.length > 0) {
              console.log(`[BLOQUEADO] Mensaje de ${telefono} bloqueado para cliente ${cliente_id}.`);
              return;
            }

            const backendResponse = await axios.post(
              "https://backend-agenda-2.onrender.com/api/webhook",
              { telefono, mensaje, cliente_id }
            );
            const respuesta = backendResponse.data && backendResponse.data.mensaje;
            if (respuesta) {
              await client.sendText(`${telefono}@c.us`, respuesta);
            }
          } catch (err) {
            console.error("Error reenviando mensaje o enviando respuesta:", err);
          }
        },
        {
          session: String(sessionId),
          userDataDir: sessionPath,
          multidevice: true,
          headless: true,
          browserArgs: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--no-zygote',
            '--single-process',
            '--disable-gpu'
          ]
        }
      );
    };

    try {
      const client = await create();
      sessions[sessionId] = client;
      console.log(`✅ Sesión ${sessionId} iniciada correctamente`);
      return client;
    } catch (err) {
      if (
        err.message.includes("SingletonLock") ||
        err.message.includes("ProcessSingleton")
      ) {
        console.warn(`🛠 Reintentando sesión ${sessionId} tras error de SingletonLock...`);
        await limpiarSingletonLock(sessionId);
        await new Promise(res => setTimeout(res, 1000));
        const client = await create(); // segundo intento
        sessions[sessionId] = client;
        console.log(`✅ Sesión ${sessionId} iniciada tras reintento`);
        return client;
      } else {
        throw err;
      }
    }
  } finally {
    sesionesEnProceso.delete(sessionId);
    await redisClient.del(redisKey); // Libera lock Redis
  }
}


// Restaurar sesiones desde Redis (para todas las que tienen info previa)
async function restaurarSesiones() {
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
  const sesionesLocales = fs.readdirSync(sessionDir).filter(f => fs.statSync(path.join(sessionDir, f)).isDirectory());
  const result = await pool.query('SELECT id FROM tenants');
  const idsValidos = result.rows.map(row => String(row.id));
  for (const sessionId of sesionesLocales) {
    if (!idsValidos.includes(sessionId)) {
      // No existe: elimina la carpeta y las claves Redis
      const dirPath = path.join(sessionDir, String(sessionId));
      if (fs.existsSync(dirPath)) {
        fs.rmSync(dirPath, { recursive: true, force: true });
        console.log(`[CLEAN] Carpeta de sesión eliminada para cliente inexistente: ${sessionId}`);
      }
      const keys = await redisClient.keys(`wppconnect:${sessionId}:*`);
      for (const key of keys) {
        await redisClient.del(key);
      }
      continue; // <-- IMPORTANTE: NO SIGUE CON LA RESTAURACIÓN DE ESTA SESIÓN
    }
    // Si ya está en memoria, no intentes restaurar
    if (sessions[sessionId]) {
      console.log(`[RESTORE] Sesión ${sessionId} ya está en memoria, no se reconecta`);
      continue;
    }
    // Elimina el SingletonLock si existe (evita errores de Chromium)
    await limpiarSingletonLock(sessionId);
    // Siempre intenta restaurar la sesión si existe la carpeta
    try {
      await crearSesionWPP(sessionId, false);
      console.log(`Restaurando sesión local ${sessionId}`);
    } catch (err) {
      console.error(`[ERROR] Error creando/restaurando sesión ${sessionId}:`, err);
    }
  }
}

// Endpoints

// Endpoint para ver todas las claves y valores de Redis de una sesión específica
app.get('/debug/redisClient/:clienteId', async (req, res) => {
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

app.get('/debug/redisClient', async (req, res) => {
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
        const estadoRedis = await redisClient.get(`wppconnect:${id}:state`);
        if (estadoRedis === 'loggedIn') {
          estado = 'CONNECTED';
        } else if (estadoRedis === 'needsQr') {
          estado = 'NEEDS_QR';
        } else if (estadoRedis === 'disconnected') {
          estado = 'DISCONNECTED';
        }
        // Si está en memoria, sobreescribe el estado
        if (sessions[id] && sessions[id].isConnected) {
          try {
            const conectado = await sessions[id].isConnected();
            enMemoria = true;
            if (conectado) {
              estado = 'CONNECTED';
            } else {
              // Si está en memoria pero no conectado, probablemente está iniciando
              estado = 'INICIANDO';
            }
          } catch {
            estado = 'ERROR';
          }
        }
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
    await limpiarSesionesOrfanasRedis(); // <--- Agrega esto aquí
    await cleanInvalidSessions();
    await reconnectLoggedSessions(
      async (base64Qr, sessionId) => { /* tu lógica de QR */ },
      async (message, client) => { /* tu lógica de mensajes */ }
    );
    console.log('✅ Sesiones logueadas restauradas');
    console.log('🚀 Inicialización completa');
  } catch (err) {
    console.error('Error durante la inicialización:', err);
  }
}

const server = app.listen(PORT).on('listening', async () => {
  console.log(`✅ WPPConnect-service corriendo en puerto ${PORT}`);
  await inicializarAplicacion();
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

app.get('/iniciar/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  try {
    await crearSesionWPP(clienteId, true); // true para forzar QR si no existe
    res.json({ ok: true, mensaje: `Sesión creada/iniciada para cliente ${clienteId}` });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

async function limpiarSesionesOrfanasRedis() {
  // Obtiene todos los IDs válidos de la base
  const result = await pool.query('SELECT id FROM tenants');
  const idsValidos = result.rows.map(row => String(row.id));

  // Busca todas las claves de sesiones en Redis
  const keys = await redisClient.keys('wppconnect:*:state');
  for (const key of keys) {
    const match = key.match(/^wppconnect:(\d+):state$/);
    if (match) {
      const sessionId = match[1];
      if (!idsValidos.includes(sessionId)) {
        // Elimina todas las claves de esa sesión
        const clavesSesion = await redisClient.keys(`wppconnect:${sessionId}:*`);
        for (const k of clavesSesion) {
          await redisClient.del(k);
        }
        console.log(`[REDIS][CLEAN] Eliminadas claves Redis de sesión huérfana: ${sessionId}`);
      }
    }
  }
}

module.exports = { pool };

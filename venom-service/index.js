require('dotenv').config();
const express = require('express');
const { Pool } = require('pg');
const fs = require('fs');
const path = require('path');
const axios = require('axios');
const redisClient = require('./redis');
const { createSession } = require('./wppconnect');
const app = express();
const PORT = process.env.PORT || 3000;
app.use(express.json());

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
});

const sessions = {};
const sessionErrors = {};

// Guardar QR en base de datos
async function guardarQR(sessionId, base64Qr) {
  const qrCodeData = base64Qr.replace(/^data:image\/\w+;base64,/, "");
  await pool.query(
    'UPDATE tenants SET qr_code = $1 WHERE id = $2',
    [qrCodeData, sessionId]
  );
}

// Crear sesión y manejar QR/mensajes
async function crearSesionWPP(sessionId, permitirGuardarQR = true) {
  if (sessions[sessionId]) return sessions[sessionId];
  const client = await createSession(
    sessionId,
    async (base64Qr) => {
      if (permitirGuardarQR) {
        await guardarQR(sessionId, base64Qr);
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
}

// Restaurar sesiones desde Redis SOLO si hay archivos guardados
async function restaurarSesiones() {
  const result = await pool.query('SELECT id FROM tenants ORDER BY id');
  const clientes = result.rows.map(r => String(r.id));
  for (const clienteId of clientes) {
    const sessionDir = path.join(__dirname, 'tokens', clienteId);
    if (fs.existsSync(sessionDir)) {
      try {
        await crearSesionWPP(clienteId, false);
      } catch (err) {
        console.error(`Error restaurando sesión ${clienteId}:`, err.message);
      }
    } else {
      console.log(`Cliente ${clienteId} no tiene archivos de sesión, no se intenta restaurar.`);
    }
  }
}

// Endpoints
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
    await crearSesionWPP(clienteId, true);
    res.json({ ok: true, mensaje: `Sesión reiniciada y QR regenerado para cliente ${clienteId}` });
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

app.get('/debug/listar-sesiones', async (req, res) => {
  let resultado = {};
  try {
    const carpetas = fs.existsSync(path.join(__dirname, 'tokens')) ? fs.readdirSync(path.join(__dirname, 'tokens')) : [];
    resultado.carpetas = carpetas.map(carpeta => {
      const carpetaPath = path.join(__dirname, 'tokens', carpeta);
      let archivos = [];
      if (fs.statSync(carpetaPath).isDirectory()) {
        archivos = fs.readdirSync(carpetaPath);
      }
      return { carpeta, archivos };
    });
  } catch (err) {
    resultado.error = err.message;
  }
  res.json(resultado);
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
        let tieneArchivos = false;
        if (sessions[id] && sessions[id].isConnected) {
          try {
            estado = (await sessions[id].isConnected()) ? 'CONNECTED' : 'DISCONNECTED';
            enMemoria = true;
          } catch {
            estado = 'ERROR';
          }
        }
        // Verifica si hay archivos de sesión en disco
        const sessionDir = path.join(__dirname, 'tokens', id);
        tieneArchivos = fs.existsSync(sessionDir);
        return {
          clienteId: id,
          nombre: row.nombre,
          comercio: row.comercio,
          estado,
          enMemoria,
          tieneArchivos,
        };
      })
    );
    res.json(estados);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Inicializar la aplicación: restaurar sesiones previas
async function inicializarAplicacion() {
  try {
    await restaurarSesiones();
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

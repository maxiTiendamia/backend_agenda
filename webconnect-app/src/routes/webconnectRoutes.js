
require('dotenv').config();
const express = require('express');
const router = express.Router();
const { pool } = require('../app/database');
const redisClient = require('../app/redisClient');
const { createSession } = require('../app/wppconnect');
const { guardarQR, limpiarQR } = require('../app/qrUtils');
const { getSessionFolder, ensureSessionFolder, limpiarSingletonLock } = require('../app/sessionUtils');
const fs = require('fs');
const path = require('path');
const axios = require('axios');

// Endpoint para obtener el estado de todas las sesiones (mock básico)
router.get('/estado-sesiones', async (req, res) => {
  try {
    const result = await pool.query('SELECT id, qr_code FROM tenants');
    const sesiones = result.rows.map(row => ({
      clienteId: row.id,
      estado: row.qr_code ? 'CONNECTED' : 'NO_INICIADA',
      enMemoria: false,
      tieneArchivos: false
    }));
    res.json(sesiones);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Endpoint para regenerar QR manualmente
router.post('/generar-qr/:sessionId', async (req, res) => {
  const { sessionId } = req.params;
  try {
    await limpiarQR(pool, sessionId);
    await limpiarSingletonLock(sessionId);
    await createSession(sessionId, async (qr) => {
      await guardarQR(pool, sessionId, qr, true);
    });
    res.json({ ok: true, message: 'QR regenerado y nueva sesión generada' });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// Endpoint para debug de errores de sesión (mock básico)
router.get('/debug/errores', async (req, res) => {
  res.json({ session_errors: {} });
});

// Endpoint para restaurar sesiones desde Redis al reiniciar el VPS
router.post('/restore-sessions', async (req, res) => {
  try {
    const keys = await redisClient.keys('session:*');
    let restauradas = 0;
    for (const key of keys) {
      const sessionData = await redisClient.get(key);
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

// Endpoint para reiniciar QR: limpia el QR viejo y genera uno nuevo
router.post('/restart-qr/:sessionId', async (req, res) => {
  const { sessionId } = req.params;
  try {
    await limpiarQR(pool, sessionId);
    // Eliminar archivos de sesión y SingletonLock
    await limpiarSingletonLock(sessionId);
    // Forzar nueva sesión y QR
    await createSession(sessionId, async (qr) => {
      await guardarQR(pool, sessionId, qr, true);
    });
    res.json({ ok: true, message: 'QR reiniciado y nueva sesión generada' });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// Middleware para guardar archivos de sesión en Redis al conectarse un cliente
async function saveSessionToRedis(sessionId) {
  const folder = getSessionFolder(sessionId);
  if (!fs.existsSync(folder)) return;
  const files = fs.readdirSync(folder).map(name => {
    const filePath = path.join(folder, name);
    return {
      name,
      data: fs.readFileSync(filePath).toString('base64')
    };
  });
  await redisClient.set(`session:${sessionId}`, JSON.stringify(files));
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

// Recibe mensajes de WhatsApp y los reenvía a la API
router.post('/webhook', async (req, res) => {
  const { sessionId, telefono, mensaje } = req.body;
  try {
    // Reenviar a la API
    const apiRes = await axios.post('http://localhost:8000/api/webhook', {
      cliente_id: sessionId,
      telefono,
      mensaje
    });
    // Aquí deberías enviar la respuesta de la API al cliente por WhatsApp usando tu sesión
    // Por ejemplo: await sendMessageToClient(sessionId, telefono, apiRes.data.mensaje);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});


module.exports = router;


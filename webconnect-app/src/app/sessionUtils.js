// Utilidades de sesión
const path = require('path');
const fs = require('fs');
const { limpiarQR } = require('./qrUtils');
const pool = require('./database'); // Ajusta si tu pool está en otro archivo
const { createSession } = require('./wppconnect'); // Ajusta según dónde esté tu createSession

// Pool de sesiones activas (importar desde wppconnect.js)
let sessions = {};

function getSessionFolder(sessionId) {
  return path.join(__dirname, '../../tokens', `session_${sessionId}`);
}

async function ensureSessionFolder(sessionId) {
  const folder = getSessionFolder(sessionId);
  if (!fs.existsSync(folder)) {
    fs.mkdirSync(folder, { recursive: true });
  }
}

async function limpiarSingletonLock(sessionId) {
  const folder = getSessionFolder(sessionId);
  const lockFile = path.join(folder, 'SingletonLock');
  
  try {
    if (fs.existsSync(lockFile)) {
      fs.unlinkSync(lockFile);
      console.log(`[WEBCONNECT] ✅ SingletonLock eliminado para sesión ${sessionId}`);
    }
    
    // También limpiar otros archivos de lock que puedan existir
    const lockPatterns = ['SingletonLock', 'SingletonSocket', 'SingletonCookie'];
    for (const pattern of lockPatterns) {
      const lockPath = path.join(folder, pattern);
      if (fs.existsSync(lockPath)) {
        fs.unlinkSync(lockPath);
        console.log(`[WEBCONNECT] ✅ ${pattern} eliminado para sesión ${sessionId}`);
      }
    }
    
  } catch (error) {
    console.error(`[WEBCONNECT] Error eliminando locks para sesión ${sessionId}:`, error);
  }
}

// Nueva función para limpiar completamente una sesión
async function limpiarSesionCompleta(sessionId, sessions) {
  try {
    if (sessions[sessionId] && typeof sessions[sessionId].close === "function") {
      await sessions[sessionId].close();
      console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} cerrada`);
    } else {
      console.warn(`[WEBCONNECT] ⚠️ No se puede cerrar sesión ${sessionId}: método close no disponible`);
    }
  } catch (e) {
    console.error(`[WEBCONNECT] Error cerrando sesión ${sessionId}:`, e.message);
  }
  // Eliminar referencia de memoria
  delete sessions[sessionId];

  // Limpiar QR en DB
  try {
    await limpiarQR(pool, sessionId);
    console.log(`[WEBCONNECT] 🗑️ QR limpiado en BD para sesión ${sessionId}`);
  } catch (dbError) {
    console.error(`[WEBCONNECT] Error limpiando QR en BD:`, dbError.message);
  }

  // Limpiar archivos de sesión
  const sessionDir = path.join(__dirname, '../../tokens', `session_${sessionId}`);
  if (fs.existsSync(sessionDir)) {
    try {
      fs.rmSync(sessionDir, { recursive: true, force: true });
      console.log(`[WEBCONNECT] 🗑️ Directorio ${sessionDir} eliminado`);
    } catch (fileError) {
      console.error(`[WEBCONNECT] Error eliminando archivos de sesión:`, fileError.message);
    }
  }
}

async function marcarSesionComoMuerta(sessionId, estados = {}) {
  estados[sessionId] = 'MUERTA';
  // Aquí podrías guardar en BD, cache o enviar alerta al admin si quieres
  // Ejemplo: await notificarAdminSesionMuerta(sessionId);
}

async function estaSesionMuerta(sessionId, estados = {}) {
  return estados[sessionId] === 'MUERTA';
}

// Función para obtener sesión activa
function getSession(sessionId) {
  // Importar sessions desde wppconnect.js si no está disponible
  try {
    const { sessions: wppSessions } = require('./wppconnect');
    sessions = wppSessions || sessions;
  } catch (e) {
    // Usar sessions local
  }
  return sessions[sessionId];
}

// Función para verificar estado real de una sesión
async function verificarEstadoSesion(sessionId) {
  const session = getSession(sessionId);
  if (!session) {
    return { estado: 'NO_ENCONTRADA', conectado: false };
  }

  try {
    const isConnected = await session.isConnected();
    const connectionState = await session.getConnectionState();
    
    return {
      estado: connectionState || (isConnected ? 'CONNECTED' : 'DISCONNECTED'),
      conectado: isConnected,
      enMemoria: true
    };
  } catch (error) {
    return {
      estado: 'ERROR',
      conectado: false,
      error: error.message
    };
  }
}

async function intentarReconectar(sessionId, onQR = null, options = {}) {
  try {
    // Chequea si la sesión está muerta antes de reconectar
    if (await estaSesionMuerta(sessionId)) {
      console.warn(`[WEBCONNECT] 🚫 Sesión ${sessionId} está muerta. No se intenta reconectar automáticamente.`);
      return;
    }

    // Opcional: Delay antes de reconectar (por ejemplo, 2 minutos tras un error)
    if (options.delayMs) {
      console.log(`[WEBCONNECT] ⏳ Esperando ${options.delayMs}ms antes de reconectar sesión ${sessionId}...`);
      await new Promise(resolve => setTimeout(resolve, options.delayMs));
    }

    console.log(`[WEBCONNECT] 🔄 Intentando reconectar sesión ${sessionId}...`);

    // Llama a createSession (o tu función de inicio de sesión)
    await createSession(sessionId, async (qr) => {
      if (onQR) {
        await onQR(qr);
      } else {
        // Guarda QR en la BD si quieres (ejemplo)
        const { guardarQR } = require('./qrUtils');
        await guardarQR(pool, sessionId, qr, true);
        console.log(`[WEBCONNECT] QR guardado en base de datos para cliente ${sessionId}`);
      }
    });

    console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} reintentada correctamente.`);
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error al intentar reconectar sesión ${sessionId}:`, error.message);
    // Puedes manejar reintentos adicionales, notificaciones, etc. aquí
  }
}

module.exports = { 
  getSessionFolder, 
  ensureSessionFolder, 
  limpiarSingletonLock,
  limpiarSesionCompleta,
  getSession,
  verificarEstadoSesion,
  marcarSesionComoMuerta,
  estaSesionMuerta,
  intentarReconectar
};
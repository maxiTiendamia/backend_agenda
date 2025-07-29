// Utilidades de sesión
const path = require('path');
const fs = require('fs');

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
  const lock = path.join(folder, 'SingletonLock');
  
  try {
    if (fs.existsSync(lock)) {
      fs.unlinkSync(lock);
      console.log(`[WEBCONNECT] SingletonLock eliminado para sesión ${sessionId}`);
    }
  } catch (error) {
    console.error(`[WEBCONNECT] Error eliminando SingletonLock para sesión ${sessionId}:`, error);
  }
}

// Nueva función para limpiar completamente una sesión
async function limpiarSesionCompleta(sessionId) {
  const folder = getSessionFolder(sessionId);
  
  try {
    if (fs.existsSync(folder)) {
      fs.rmSync(folder, { recursive: true, force: true });
      console.log(`[WEBCONNECT] Directorio de sesión ${sessionId} eliminado completamente`);
    }
  } catch (error) {
    console.error(`[WEBCONNECT] Error eliminando directorio de sesión ${sessionId}:`, error);
  }
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

module.exports = { 
  getSessionFolder, 
  ensureSessionFolder, 
  limpiarSingletonLock,
  limpiarSesionCompleta,
  getSession,
  verificarEstadoSesion
};

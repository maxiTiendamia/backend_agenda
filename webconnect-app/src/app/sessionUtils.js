// Utilidades de sesión
const path = require('path');
const fs = require('fs');

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

module.exports = { 
  getSessionFolder, 
  ensureSessionFolder, 
  limpiarSingletonLock,
  limpiarSesionCompleta 
};

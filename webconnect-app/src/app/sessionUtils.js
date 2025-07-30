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
async function limpiarSesionCompleta(sessionId) {
  try {
    console.log(`[WEBCONNECT] 🧹 Limpieza completa de sesión ${sessionId}...`);
    
    // 1. Cerrar sesión en memoria si existe
    const { sessions } = require('./wppconnect');
    if (sessions && sessions[sessionId]) {
      try {
        await sessions[sessionId].close();
        console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} cerrada`);
      } catch (e) {
        console.error(`[WEBCONNECT] Error cerrando sesión ${sessionId}:`, e.message);
      }
      delete sessions[sessionId];
    }
    
    // 2. Esperar a que se liberen recursos
    await new Promise(resolve => setTimeout(resolve, 1500));
    
    // 3. Eliminar directorio completo
    const folder = getSessionFolder(sessionId);
    if (fs.existsSync(folder)) {
      fs.rmSync(folder, { recursive: true, force: true });
      console.log(`[WEBCONNECT] 🗑️ Directorio eliminado: ${folder}`);
    }
    
    // 4. Recrear directorio limpio
    await ensureSessionFolder(sessionId);
    console.log(`[WEBCONNECT] 📁 Directorio recreado limpio para sesión ${sessionId}`);
    
    return true;
  } catch (error) {
    console.error(`[WEBCONNECT] Error en limpieza completa de sesión ${sessionId}:`, error);
    return false;
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

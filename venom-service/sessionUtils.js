const fs = require('fs-extra');
const path = require('path');
const redisClient = require('./redisClient'); // Ajusta según tu proyecto

function getSessionFolder(sessionId) {
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
  return path.join(sessionDir, String(sessionId));
}

async function limpiarSesion(sessionId) {
  // Elimina carpetas en disco
  const basePath = path.join(__dirname, 'tokens', String(sessionId));
  await fs.remove(basePath);
  console.log(`[SESSION][DISK] Carpeta de sesión ${sessionId} eliminada completamente`);

  // Limpia claves en Redis
  await redisClient.del(`session:${sessionId}:state`);
  await redisClient.del(`session:${sessionId}:hasSession`);
  await redisClient.del(`session:${sessionId}:needsQr`);
  await redisClient.del(`session:${sessionId}:disconnectReason`);
  console.log(`[REDIS] Claves de sesión ${sessionId} eliminadas`);
}

module.exports = { getSessionFolder, limpiarSesion };

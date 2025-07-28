const fs = require('fs-extra');
const path = require('path');
const redisClient = require('./redisClient'); // Usa el nombre correcto de tu archivo

function getSessionFolder(sessionId) {
  const base = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
  // Usa una carpeta por sesión, por ejemplo /tokens/52
  return path.join(base, String(sessionId));
}

// Limpia solo la carpeta de sesión en disco
async function cleanSessionFolder(sessionId) {
  const basePath = getSessionFolder(sessionId);
  if (await fs.pathExists(basePath)) {
    await fs.remove(basePath);
    console.log(`[SESSION][DISK] Carpeta de sesión ${sessionId} eliminada completamente`);
  }
}

async function limpiarSesion(sessionId) {
  await cleanSessionFolder(sessionId);
  // Limpia claves en Redis
  await redisClient.del(`session:${sessionId}:state`);
  await redisClient.del(`session:${sessionId}:hasSession`);
  await redisClient.del(`session:${sessionId}:needsQr`);
  await redisClient.del(`session:${sessionId}:disconnectReason`);
  console.log(`[REDIS] Claves de sesión ${sessionId} eliminadas`);
}

module.exports = { getSessionFolder, limpiarSesion, cleanSessionFolder };

const fs = require('fs-extra');
const path = require('path');
const redisClient = require('./redisClient'); // Usa el nombre correcto de tu archivo

function getSessionFolder(sessionId) {
  const base = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
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

async function ensureSessionFolder(sessionId) {
  const folder = getSessionFolder(sessionId);
  if (!(await fs.pathExists(folder))) {
    await fs.mkdirp(folder);
    console.log(`[SESSION][DISK] Carpeta creada para sesión ${sessionId}: ${folder}`);
  }
}

module.exports = { getSessionFolder, limpiarSesion, cleanSessionFolder, ensureSessionFolder };

// Utilidades de sesión
const path = require('path');
const fs = require('fs');
function getSessionFolder(sessionId) {
  return path.join(__dirname, '../../sessions', sessionId);
}
async function ensureSessionFolder(sessionId) {
  const folder = getSessionFolder(sessionId);
  if (!fs.existsSync(folder)) fs.mkdirSync(folder, { recursive: true });
}
async function limpiarSingletonLock(sessionId) {
  // Simulación: elimina archivo de lock si existe
  const lock = path.join(getSessionFolder(sessionId), 'SingletonLock');
  if (fs.existsSync(lock)) fs.unlinkSync(lock);
}
module.exports = { getSessionFolder, ensureSessionFolder, limpiarSingletonLock };

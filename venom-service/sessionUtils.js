const fs = require('fs');
const path = require('path');
const fsExtra = require('fs-extra');

// Devuelve la ruta completa a la carpeta de sesión para un sessionId dado
function getSessionFolder(sessionId) {
  const base = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
  return path.join(base, String(sessionId));
}

// Crea la carpeta de sesión si no existe
async function ensureSessionFolder(sessionId) {
  const folder = getSessionFolder(sessionId);
  if (!(await fsExtra.pathExists(folder))) {
    await fsExtra.mkdirp(folder);
    console.log(`[SESSION][DISK] Carpeta creada para sesión ${sessionId}: ${folder}`);
  }
}

// Limpia todos los archivos "SingletonLock" dentro de la carpeta de sesión y subcarpetas
async function limpiarSingletonLock(sessionId) {
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
  const basePath = path.join(sessionDir, sessionId);
  if (!fs.existsSync(basePath)) return;

  function buscarYEliminar(dir) {
    let files;
    try {
      files = fs.readdirSync(dir);
    } catch (err) {
      // Si la carpeta no existe o error, salir silenciosamente
      return;
    }
    for (const file of files) {
      const fullPath = path.join(dir, file);
      if (!fs.existsSync(fullPath)) continue;
      if (fs.statSync(fullPath).isDirectory()) {
        buscarYEliminar(fullPath);
      } else if (file === 'SingletonLock') {
        try {
          fs.unlinkSync(fullPath);
          console.log(`🔓 SingletonLock eliminado en ${fullPath} para cliente ${sessionId}`);
        } catch (err) {
          console.error(`❌ Error eliminando SingletonLock en ${fullPath} para ${sessionId}:`, err.message);
        }
      }
    }
  }
  buscarYEliminar(basePath);
}

module.exports = {
  getSessionFolder,
  ensureSessionFolder,
  limpiarSingletonLock
};

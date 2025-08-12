const fs = require('fs');
const path = require('path');

function getProfileDir(sessionId) {
  // Ruta a tokens desde esta carpeta: webconnect-app/src/services -> ../../tokens
  return path.join(__dirname, '../../tokens', `session_${sessionId}`);
}

/**
 * Si hay un SingletonLock (u otros locks) en el perfil, elimina todo el directorio
 * para permitir que Chromium/Puppeteer se inicie limpio.
 */
function ensureProfileDirClean(sessionId, logger = console) {
  const dir = getProfileDir(sessionId);
  if (!fs.existsSync(dir)) return;

  const candidates = [
    path.join(dir, 'SingletonLock'),
    path.join(dir, 'SingletonSocket'),
    path.join(dir, 'SingletonCookie'),
    path.join(dir, 'Default', 'SingletonLock'),
    path.join(dir, 'Default', 'SingletonSocket'),
    path.join(dir, 'Default', 'SingletonCookie'),
  ];

  const hasAnyLock = candidates.some((p) => {
    try { return fs.existsSync(p); } catch (_) { return false; }
  });

  if (hasAnyLock) {
    try {
      fs.rmSync(dir, { recursive: true, force: true });
      logger.info?.(`[INIT] 🗑️ Removido perfil con locks para tenant ${sessionId} (${dir})`);
    } catch (e) {
      logger.error?.(`[INIT] ❌ Error removiendo perfil ${dir}: ${e.message}`);
    }
  }
}

module.exports = {
  ensureProfileDirClean,
  getProfileDir,
};
const fs = require('fs');
const path = require('path');

function getProfileDir(sessionId) {
  // Ruta a tokens desde esta carpeta: webconnect-app/src/services -> ../../tokens
  return path.join(__dirname, '../../tokens', `session_${sessionId}`);
}

/**
 * Limpia archivos de lock del perfil sin borrar el directorio completo.
 * Evita pérdidas de sesión por falsos positivos.
 */
function ensureProfileDirClean(sessionId, logger = console) {
  const dir = getProfileDir(sessionId);
  if (!fs.existsSync(dir)) return;

  try {
    // Preferir utilidades centralizadas
    const { limpiarSingletonLock } = require('../app/sessionUtils');
    limpiarSingletonLock(sessionId)
      .then(() => logger.info?.(`[INIT] 🧹 Locks limpiados para tenant ${sessionId} (${dir})`))
      .catch((e) => logger.warn?.(`[INIT] ⚠️ Error limpiando locks para ${sessionId}: ${e.message}`));
  } catch (_) {
    // Fallback: eliminar archivos de lock conocidos sin borrar el perfil
    const candidates = [
      path.join(dir, 'SingletonLock'),
      path.join(dir, 'SingletonSocket'),
      path.join(dir, 'SingletonCookie'),
      path.join(dir, 'Default', 'SingletonLock'),
      path.join(dir, 'Default', 'SingletonSocket'),
      path.join(dir, 'Default', 'SingletonCookie'),
    ];
    let removed = 0;
    for (const p of candidates) {
      try {
        if (fs.existsSync(p)) {
          fs.rmSync(p, { force: true });
          removed++;
        }
      } catch (e) {
        logger.warn?.(`[INIT] ⚠️ No se pudo eliminar ${p}: ${e.message}`);
      }
    }
    if (removed > 0) {
      logger.info?.(`[INIT] 🧹 Eliminados ${removed} lock(s) para tenant ${sessionId} (${dir})`);
    }
  }
}

module.exports = {
  ensureProfileDirClean,
  getProfileDir,
};
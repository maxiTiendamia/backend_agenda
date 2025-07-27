const fs = require('fs');
const path = require('path');

function getSessionFolder(sessionId) {
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
  return path.join(sessionDir, String(sessionId));
}

function cleanSessionFolder(sessionId) {
  const folder = getSessionFolder(sessionId);
  if (fs.existsSync(folder)) {
    // Elimina SingletonLock si existe
    const singletonLockPath = path.join(folder, 'SingletonLock');
    if (fs.existsSync(singletonLockPath)) {
      fs.unlinkSync(singletonLockPath);
    }
    fs.rmSync(folder, { recursive: true, force: true });
    console.log(`[SESSION] Carpeta de sesión ${sessionId} eliminada`);
  }
  console.log(`[DEBUG] sessionId=${sessionId}, folder=${getSessionFolder(sessionId)}`);
}

module.exports = { getSessionFolder, cleanSessionFolder };

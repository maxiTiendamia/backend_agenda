const fs = require('fs');
const path = require('path');

function getSessionFolder(sessionId) {
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
  return path.join(sessionDir, String(sessionId));
}

function cleanSessionFolder(sessionId) {
  const folder = path.join(process.env.SESSION_FOLDER || path.join(__dirname, 'tokens'), String(sessionId));
  if (fs.existsSync(folder)) {
    fs.rmSync(folder, { recursive: true, force: true });
    console.log(`[SESSION][DISK] Carpeta de sesión ${sessionId} eliminada completamente`);
  }
}

module.exports = { getSessionFolder, cleanSessionFolder };

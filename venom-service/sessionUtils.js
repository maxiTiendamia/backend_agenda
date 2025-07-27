const fs = require('fs');
const path = require('path');

function getSessionFolder(sessionId) {
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
  return path.join(sessionDir, String(sessionId));
}

function cleanSessionFolder(sessionId) {
  const folder = getSessionFolder(sessionId);
  if (fs.existsSync(folder)) {
    fs.rmSync(folder, { recursive: true, force: true });
    console.log(`[SESSION] Carpeta de sesión ${sessionId} eliminada`);
  }
}

module.exports = { getSessionFolder, cleanSessionFolder };

const fs = require('fs');
const path = require('path');

function getSessionFolder(sessionId) {
  const base = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
  return path.join(base, String(sessionId));
}

function cleanSessionFolder(sessionId) {
  const folder = getSessionFolder(sessionId);
  if (fs.existsSync(folder)) {
    fs.rmSync(folder, { recursive: true, force: true });
    console.log(`[FS] Carpeta de sesión eliminada: ${folder}`);
  }
}

module.exports = { getSessionFolder, cleanSessionFolder };

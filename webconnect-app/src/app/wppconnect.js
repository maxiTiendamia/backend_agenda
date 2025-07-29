// Genera un QR real de WhatsApp usando @wppconnect-team/wppconnect
const wppconnect = require('@wppconnect-team/wppconnect');
const path = require('path');

// Objeto para gestionar las instancias activas por sesión
const sessions = {};

/**
 * Crea una sesión de WhatsApp y la guarda en el objeto sessions.
 * @param {string|number} sessionId - ID de la sesión/cliente
 * @param {function} onQR - Callback que recibe el QR generado
 * @returns {Promise<object>} - Cliente de wppconnect
 */
async function createSession(sessionId, onQR) {
  // Asegurar que cada sesión tenga su propio directorio
  const sessionDir = path.join(__dirname, '../../tokens', `session_${sessionId}`);
  
  try {
    const client = await wppconnect.create({
      session: `session_${sessionId}`, // Nombre único de sesión
      folderNameToken: sessionDir, // Directorio único para tokens
      mkdirFolderToken: true, // Crear directorio si no existe
      headless: true,
      devtools: false,
      useChrome: false,
      puppeteerOptions: {
        userDataDir: sessionDir, // Directorio único de datos del usuario
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-accelerated-2d-canvas',
          '--no-first-run',
          '--no-zygote',
          '--single-process',
          '--disable-gpu',
          '--disable-web-security',
          '--disable-features=VizDisplayCompositor',
          `--user-data-dir=${sessionDir}` // Asegurar directorio único
        ]
      },
      catchQR: async (qrCode, asciiQR, attempts, urlCode) => {
        console.log(`[WEBCONNECT] QR generado para sesión ${sessionId}, intento ${attempts}`);
        if (onQR) {
          await onQR(qrCode);
        }
      },
      statusFind: (statusSession, session) => {
        console.log(`[WEBCONNECT] Estado de sesión ${sessionId}: ${statusSession}`);
        if (statusSession === 'qrReadSuccess') {
          console.log(`[WEBCONNECT] ✅ QR escaneado exitosamente para sesión ${sessionId}`);
        } else if (statusSession === 'qrReadFail') {
          console.log(`[WEBCONNECT] ❌ Fallo al leer QR para sesión ${sessionId}`);
        }
      }
    });

    console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} creada exitosamente`);

    // Guardar la instancia en sessions
    sessions[sessionId] = client;

    // Configurar eventos del cliente (opcional)
    client.onMessage(async (message) => {
      console.log(`[WEBCONNECT] Mensaje recibido en sesión ${sessionId}:`, message.body);
    });

    return client;
    
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error creando sesión ${sessionId}:`, error);
    throw error;
  }
}

/**
 * Devuelve la instancia activa de WhatsApp para un sessionId.
 * @param {string|number} sessionId
 * @returns {object|undefined}
 */
function getSession(sessionId) {
  return sessions[sessionId];
}

/**
 * Limpia la sesión específica y la elimina del pool de sesiones.
 * @param {string|number} sessionId
 */
async function clearSession(sessionId) {
  const sessionDir = path.join(__dirname, '../../tokens', `session_${sessionId}`);
  const fs = require('fs').promises;
  
  try {
    const lockFile = path.join(sessionDir, 'SingletonLock');
    try {
      await fs.unlink(lockFile);
      console.log(`[WEBCONNECT] SingletonLock eliminado para sesión ${sessionId}`);
    } catch (err) {
      // Archivo no existe, no es problema
    }
    // Eliminar del pool en memoria
    delete sessions[sessionId];

    // Opcional: eliminar todo el directorio de la sesión
    // await fs.rmdir(sessionDir, { recursive: true });
  } catch (error) {
    console.error(`[WEBCONNECT] Error limpiando sesión ${sessionId}:`, error);
  }
}

module.exports = { createSession, clearSession, getSession };
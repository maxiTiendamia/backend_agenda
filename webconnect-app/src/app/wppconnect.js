// Genera un QR real de WhatsApp usando @wppconnect-team/wppconnect
const wppconnect = require('@wppconnect-team/wppconnect');
const path = require('path');

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
        
        // Manejar diferentes estados
        if (statusSession === 'qrReadSuccess') {
          console.log(`[WEBCONNECT] ✅ QR escaneado exitosamente para sesión ${sessionId}`);
        } else if (statusSession === 'qrReadFail') {
          console.log(`[WEBCONNECT] ❌ Fallo al leer QR para sesión ${sessionId}`);
        }
      }
    });

    console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} creada exitosamente`);
    
    // Configurar eventos del cliente
    client.onMessage(async (message) => {
      // Aquí puedes manejar mensajes entrantes
      console.log(`[WEBCONNECT] Mensaje recibido en sesión ${sessionId}:`, message.body);
    });

    return client;
    
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error creando sesión ${sessionId}:`, error);
    throw error;
  }
}

// Función para limpiar sesión específica
async function clearSession(sessionId) {
  const sessionDir = path.join(__dirname, '../../tokens', `session_${sessionId}`);
  const fs = require('fs').promises;
  
  try {
    // Eliminar el archivo SingletonLock específico
    const lockFile = path.join(sessionDir, 'SingletonLock');
    try {
      await fs.unlink(lockFile);
      console.log(`[WEBCONNECT] SingletonLock eliminado para sesión ${sessionId}`);
    } catch (err) {
      // Archivo no existe, no es problema
    }
    
    // Opcionalmente eliminar todo el directorio de la sesión
    // await fs.rmdir(sessionDir, { recursive: true });
    
  } catch (error) {
    console.error(`[WEBCONNECT] Error limpiando sesión ${sessionId}:`, error);
  }
}

module.exports = { createSession, clearSession };

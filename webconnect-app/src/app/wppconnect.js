// Genera un QR real de WhatsApp usando @wppconnect-team/wppconnect
const wppconnect = require('@wppconnect-team/wppconnect');
const path = require('path');
const axios = require('axios'); // Asegúrate de instalarlo: npm install axios

// Objeto para gestionar las instancias activas por sesión
const sessions = {};

// URL de tu API FastAPI en Render
const API_URL = process.env.API_URL || 'https://backend-agenda-2.onrender.com';

/**
 * Función para procesar mensaje y obtener respuesta de la API
 */
async function procesarMensaje(sessionId, mensaje, client) {
  try {
    const { from, body, type, isGroupMsg } = mensaje;
    
    // Solo procesar mensajes de texto y que no sean de grupos
    if (type !== 'chat' || isGroupMsg) {
      console.log(`[WEBCONNECT] Mensaje ignorado - Tipo: ${type}, Grupo: ${isGroupMsg}`);
      return;
    }

    console.log(`[WEBCONNECT] Procesando mensaje de ${from}: ${body}`);

    // Extraer número de teléfono limpio (sin @c.us)
    const telefono = from.replace('@c.us', '');

    // Hacer request a tu API FastAPI en Render
    const response = await axios.post(`${API_URL}/api/webhook`, {
      cliente_id: sessionId, // Usar sessionId como cliente_id
      telefono: telefono,
      mensaje: body
    }, {
      timeout: 30000, // 30 segundos timeout
      headers: {
        'Content-Type': 'application/json'
      }
    });

    // Verificar si hay respuesta de la API
    if (response.data && response.data.mensaje && response.data.mensaje.trim() !== '') {
      // Enviar la respuesta de vuelta al cliente
      await client.sendText(from, response.data.mensaje);
      console.log(`[WEBCONNECT] ✅ Respuesta enviada a ${telefono}: ${response.data.mensaje}`);
    } else {
      console.log(`[WEBCONNECT] ⚠️ Sin respuesta para enviar a ${telefono}`);
    }

  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error procesando mensaje para sesión ${sessionId}:`, error.message);
    
    // Log más detallado del error
    if (error.response) {
      console.error(`[WEBCONNECT] Error de respuesta: ${error.response.status} - ${error.response.data}`);
    } else if (error.request) {
      console.error(`[WEBCONNECT] Error de red:`, error.request);
    }
    
    // Si es error de conexión con la API, enviar mensaje de error
    if (error.code === 'ECONNREFUSED' || error.code === 'ENOTFOUND' || error.response?.status >= 500) {
      try {
        await client.sendText(mensaje.from, 'Lo siento, nuestro sistema está temporalmente fuera de servicio. Por favor intenta más tarde.');
      } catch (sendError) {
        console.error(`[WEBCONNECT] Error enviando mensaje de error:`, sendError);
      }
    }
  }
}

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
        } else if (statusSession === 'isLogged') {
          console.log(`[WEBCONNECT] 📱 Sesión ${sessionId} ya está logueada`);
        }
      }
    });

    console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} creada exitosamente`);

    // Guardar la instancia en sessions
    sessions[sessionId] = client;

    // 🔥 CONFIGURAR EVENTOS DEL CLIENTE - ACTUALIZADO
    client.onMessage(async (message) => {
      console.log(`[WEBCONNECT] 📨 Mensaje recibido en sesión ${sessionId}:`, message.body);
      
      // ✨ Procesar mensaje y enviar respuesta automática usando tu API
      await procesarMensaje(sessionId, message, client);
    });

    // Evento para cambios de estado
    client.onStateChange((state) => {
      console.log(`[WEBCONNECT] 🔄 Estado de conexión sesión ${sessionId}:`, state);
    });

    // Evento cuando el cliente está listo
    client.onReady(() => {
      console.log(`[WEBCONNECT] 🚀 Cliente ${sessionId} listo para enviar/recibir mensajes`);
      console.log(`[WEBCONNECT] 🌐 Conectado a API: ${API_URL}`);
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
    // Cerrar cliente si existe
    if (sessions[sessionId]) {
      try {
        await sessions[sessionId].close();
        console.log(`[WEBCONNECT] Cliente ${sessionId} cerrado`);
      } catch (closeError) {
        console.error(`[WEBCONNECT] Error cerrando cliente ${sessionId}:`, closeError);
      }
    }

    // Eliminar del pool en memoria
    delete sessions[sessionId];

    // Limpiar archivos de sesión
    const lockFile = path.join(sessionDir, 'SingletonLock');
    try {
      await fs.unlink(lockFile);
      console.log(`[WEBCONNECT] SingletonLock eliminado para sesión ${sessionId}`);
    } catch (err) {
      // Archivo no existe, no es problema
    }
  } catch (error) {
    console.error(`[WEBCONNECT] Error limpiando sesión ${sessionId}:`, error);
  }
}

/**
 * Envía un mensaje desde el servidor (función auxiliar)
 * @param {string|number} sessionId 
 * @param {string} to - Número de teléfono
 * @param {string} message - Mensaje a enviar
 */
async function sendMessage(sessionId, to, message) {
  try {
    const client = sessions[sessionId];
    if (!client) {
      throw new Error(`Sesión ${sessionId} no encontrada`);
    }

    const formattedTo = to.includes('@c.us') ? to : `${to}@c.us`;
    await client.sendText(formattedTo, message);
    console.log(`[WEBCONNECT] ✅ Mensaje enviado desde sesión ${sessionId} a ${to}: ${message}`);
    return true;
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error enviando mensaje desde sesión ${sessionId} a ${to}:`, error);
    return false;
  }
}

/**
 * Función para probar conectividad con la API
 */
async function testAPIConnection() {
  try {
    console.log(`[WEBCONNECT] 🔍 Probando conexión con API: ${API_URL}`);
    const response = await axios.get(`${API_URL}/`, { timeout: 10000 });
    console.log(`[WEBCONNECT] ✅ API respondió:`, response.data);
    return true;
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error conectando con API:`, error.message);
    return false;
  }
}

module.exports = { 
  createSession, 
  clearSession, 
  getSession, 
  sendMessage, 
  testAPIConnection,
  sessions // Exportar el objeto sessions para acceso externo
};
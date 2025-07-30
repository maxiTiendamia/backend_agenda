// Genera un QR real de WhatsApp usando @wppconnect-team/wppconnect
const wppconnect = require('@wppconnect-team/wppconnect');
const path = require('path');
const axios = require('axios'); // Asegúrate de instalarlo: npm install axios
const { Pool } = require('pg');

// Objeto para gestionar las instancias activas por sesión
const sessions = {};

// URL de tu API FastAPI en Render
const API_URL = process.env.API_URL || 'https://backend-agenda-2.onrender.com';

/**
 * Pool de conexiones compartido para verificaciones
 */
const verificationPool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
  max: 3, // Máximo 3 conexiones para verificaciones
  idleTimeoutMillis: 30000
});

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

    // 🔥 NUEVA VALIDACIÓN: Verificar números bloqueados ANTES de procesar
    const esBloqueado = await verificarNumeroBloqueado(telefono, sessionId);
    if (esBloqueado) {
      console.log(`🚫 [WEBCONNECT] Número ${telefono} bloqueado para cliente ${sessionId} - No se procesará`);
      return; // Salir sin procesar ni responder
    }
    
    // Si no está bloqueado, continuar con el flujo normal
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
 * Verificar si un número está bloqueado (versión optimizada)
 */
async function verificarNumeroBloqueado(telefono, clienteId) {
  try {
    const result = await verificationPool.query(`
      SELECT id, empleado_id 
      FROM blocked_numbers 
      WHERE telefono = $1 AND cliente_id = $2
    `, [telefono, clienteId]);

    if (result.rows.length > 0) {
      const tipos_bloqueo = result.rows.map(row => 
        row.empleado_id ? `empleado_${row.empleado_id}` : 'nivel_cliente'
      );
      
      console.log(`🚫 [WEBCONNECT] Número ${telefono} bloqueado para cliente ${clienteId} (${tipos_bloqueo.join(', ')})`);
      return true;
    }
    
    return false;

  } catch (error) {
    console.error(`[WEBCONNECT] Error verificando número bloqueado:`, error);
    return false; // Fail-safe: permitir mensaje si hay error
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
          '--memory-pressure-off', // Prevenir cierre por memoria
          '--max-old-space-size=512', // Limitar uso de memoria
          `--user-data-dir=${sessionDir}`
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
        } else if (statusSession === 'notLogged') {
          console.log(`[WEBCONNECT] 🔒 Sesión ${sessionId} no está logueada`);
        } else if (statusSession === 'connectSuccess') {
          console.log(`[WEBCONNECT] 🚀 Cliente ${sessionId} conectado y listo`);
        } else if (statusSession === 'browserClose') {
          console.log(`[WEBCONNECT] 🔴 Browser cerrado para sesión ${sessionId} - Intentando reconectar...`);
          
          // 🔥 AUTO-RECONEXIÓN cuando se cierra el navegador
          setTimeout(async () => {
            try {
              console.log(`[WEBCONNECT] 🔄 Iniciando reconexión automática para sesión ${sessionId}...`);
              
              // Eliminar sesión de memoria
              if (sessions[sessionId]) {
                delete sessions[sessionId];
              }
              
              // Esperar un poco antes de reconectar
              await new Promise(resolve => setTimeout(resolve, 3000));
              
              // Intentar reconectar
              await createSession(sessionId, null);
              console.log(`[WEBCONNECT] ✅ Reconexión exitosa para sesión ${sessionId}`);
              
            } catch (reconnectError) {
              console.error(`[WEBCONNECT] ❌ Error en reconexión automática para sesión ${sessionId}:`, reconnectError.message);
              
              // Si falla la reconexión, programar otro intento en 1 minuto
              setTimeout(async () => {
                try {
                  console.log(`[WEBCONNECT] 🔄 Segundo intento de reconexión para sesión ${sessionId}...`);
                  await createSession(sessionId, null);
                  console.log(`[WEBCONNECT] ✅ Segundo intento exitoso para sesión ${sessionId}`);
                } catch (secondError) {
                  console.error(`[WEBCONNECT] ❌ Segundo intento fallido para sesión ${sessionId}:`, secondError.message);
                }
              }, 60000); // 1 minuto
            }
          }, 5000); // 5 segundos
        }
      }
    });

    console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} creada exitosamente`);

    // Guardar la instancia en sessions
    sessions[sessionId] = client;

    // 🔥 CONFIGURAR EVENTOS DEL CLIENTE
    client.onMessage(async (message) => {
      console.log(`[WEBCONNECT] 📨 Mensaje recibido en sesión ${sessionId}:`, message.body);
      
      // ✨ Procesar mensaje y enviar respuesta automática usando tu API
      await procesarMensaje(sessionId, message, client);
    });

    // Evento para cambios de estado
    client.onStateChange((state) => {
      console.log(`[WEBCONNECT] 🔄 Estado de conexión sesión ${sessionId}:`, state);
      
      // Manejar diferentes estados
      if (state === 'CONNECTED') {
        console.log(`[WEBCONNECT] 🚀 Cliente ${sessionId} listo para enviar/recibir mensajes`);
        console.log(`[WEBCONNECT] 🌐 Conectado a API: ${API_URL}`);
      } else if (state === 'DISCONNECTED') {
        console.log(`[WEBCONNECT] 🔴 Cliente ${sessionId} desconectado - Verificando si necesita reconexión...`);
        
        // Programar verificación de reconexión si está desconectado por mucho tiempo
        setTimeout(async () => {
          if (sessions[sessionId] && state === 'DISCONNECTED') {
            console.log(`[WEBCONNECT] ⚠️ Sesión ${sessionId} sigue desconectada, iniciando reconexión...`);
            try {
              // Eliminar sesión actual
              if (sessions[sessionId]) {
                try {
                  await sessions[sessionId].close();
                } catch (e) {
                  // Ignorar errores al cerrar
                }
                delete sessions[sessionId];
              }
              
              // Crear nueva sesión
              await createSession(sessionId, null);
              console.log(`[WEBCONNECT] ✅ Reconexión por desconexión exitosa para sesión ${sessionId}`);
            } catch (reconnectError) {
              console.error(`[WEBCONNECT] ❌ Error en reconexión por desconexión para sesión ${sessionId}:`, reconnectError.message);
            }
          }
        }, 30000); // 30 segundos
        
      } else if (state === 'PAIRING') {
        console.log(`[WEBCONNECT] 🔗 Cliente ${sessionId} en proceso de emparejamiento`);
      }
    });

    // Verificar si el cliente tiene otros eventos disponibles
    if (typeof client.onDisconnected === 'function') {
      client.onDisconnected(() => {
        console.log(`[WEBCONNECT] 🔴 Cliente ${sessionId} desconectado (onDisconnected)`);
      });
    }

    if (typeof client.onInterfaceChange === 'function') {
      client.onInterfaceChange((interfaceState) => {
        console.log(`[WEBCONNECT] 🔄 Cambio de interfaz ${sessionId}:`, interfaceState);
      });
    }

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

/**
 * Inicializa sesiones existentes al arrancar la aplicación
 * Ahora verifica contra la base de datos antes de restaurar
 */
async function initializeExistingSessions() {
  const fs = require('fs');
  const { Pool } = require('pg');
  const tokensDir = path.join(__dirname, '../../tokens');
  
  try {
    if (!fs.existsSync(tokensDir)) {
      console.log('[WEBCONNECT] 📁 No hay directorio de tokens');
      return;
    }

    // Obtener lista de directorios de sesión existentes
    const sessionDirs = fs.readdirSync(tokensDir)
      .filter(dir => dir.startsWith('session_'))
      .map(dir => dir.replace('session_', ''));

    console.log(`[WEBCONNECT] 🔍 Directorios de sesión encontrados: [${sessionDirs.join(', ')}]`);

    if (sessionDirs.length === 0) {
      console.log('[WEBCONNECT] ⚪ No hay directorios de sesión para restaurar');
      return;
    }

    // Conectar a la base de datos para verificar qué clientes existen
    const pool = new Pool({
      connectionString: process.env.DATABASE_URL,
      ssl: {
        rejectUnauthorized: false
      }
    });

    let dbClient = null;
    let clientesActivos = [];

    try {
      dbClient = await pool.connect();
      const result = await dbClient.query('SELECT id FROM tenants');
      clientesActivos = result.rows.map(tenant => tenant.id.toString());
      console.log(`[WEBCONNECT] 📊 Clientes activos en BD: [${clientesActivos.join(', ')}]`);
    } catch (dbError) {
      console.error('[WEBCONNECT] ❌ Error consultando base de datos:', dbError.message);
      return;
    } finally {
      if (dbClient) {
        dbClient.release();
      }
      await pool.end();
    }

    // Limpiar directorios de sesiones que ya no existen en la BD
    const sessionesObsoletas = sessionDirs.filter(sessionId => !clientesActivos.includes(sessionId));
    const sessionesValidas = sessionDirs.filter(sessionId => clientesActivos.includes(sessionId));

    // Eliminar directorios obsoletos
    if (sessionesObsoletas.length > 0) {
      console.log(`[WEBCONNECT] 🗑️ Eliminando ${sessionesObsoletas.length} directorios obsoletos: [${sessionesObsoletas.join(', ')}]`);
      
      for (const sessionId of sessionesObsoletas) {
        try {
          const sessionDir = path.join(tokensDir, `session_${sessionId}`);
          fs.rmSync(sessionDir, { recursive: true, force: true });
          console.log(`[WEBCONNECT] ❌ Directorio eliminado: session_${sessionId}`);
        } catch (delError) {
          console.error(`[WEBCONNECT] Error eliminando directorio session_${sessionId}:`, delError.message);
        }
      }
    }

    // Restaurar solo las sesiones válidas
    if (sessionesValidas.length > 0) {
      console.log(`[WEBCONNECT] ✅ Restaurando ${sessionesValidas.length} sesiones válidas: [${sessionesValidas.join(', ')}]`);
      
      for (const sessionId of sessionesValidas) {
        try {
          console.log(`[WEBCONNECT] 🔄 Restaurando sesión ${sessionId}...`);
          
          // Crear sesión sin callback de QR (ya está autenticada)
          await createSession(sessionId, null);
          
          console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} restaurada`);
        } catch (error) {
          console.error(`[WEBCONNECT] ❌ Error restaurando sesión ${sessionId}:`, error.message);
          
          // Si hay error restaurando, eliminar el directorio de tokens de esa sesión
          try {
            const sessionDir = path.join(tokensDir, `session_${sessionId}`);
            fs.rmSync(sessionDir, { recursive: true, force: true });
            console.log(`[WEBCONNECT] 🗑️ Directorio corrupto eliminado: session_${sessionId}`);
          } catch (delError) {
            console.error(`[WEBCONNECT] Error eliminando directorio corrupto session_${sessionId}:`, delError.message);
          }
        }
      }
    } else {
      console.log('[WEBCONNECT] ⚪ No hay sesiones válidas para restaurar');
    }

  } catch (error) {
    console.error('[WEBCONNECT] ❌ Error inicializando sesiones:', error);
  }
}

/**
 * Monitorea el estado de las sesiones y reconecta automáticamente si es necesario
 */
async function monitorearSesiones() {
  console.log('[WEBCONNECT] 🔍 Iniciando monitoreo de sesiones...');
  
  setInterval(async () => {
    try {
      const sesionesActivas = Object.keys(sessions);
      console.log(`[WEBCONNECT] 📊 Monitoreando ${sesionesActivas.length} sesiones activas...`);
      
      for (const sessionId of sesionesActivas) {
        try {
          const client = sessions[sessionId];
          if (!client) continue;
          
          // Verificar si la sesión está conectada
          const isConnected = await client.isConnected();
          
          if (!isConnected) {
            console.log(`[WEBCONNECT] ⚠️ Sesión ${sessionId} no está conectada, verificando estado...`);
            
            const connectionState = await client.getConnectionState();
            console.log(`[WEBCONNECT] Estado actual de sesión ${sessionId}: ${connectionState}`);
            
            // Si está completamente desconectado, intentar reconectar
            if (connectionState === 'DISCONNECTED' || connectionState === 'TIMEOUT') {
              console.log(`[WEBCONNECT] 🔄 Iniciando reconexión automática para sesión ${sessionId}...`);
              
              // Cerrar sesión actual
              try {
                await client.close();
              } catch (e) {
                // Ignorar errores al cerrar
              }
              delete sessions[sessionId];
              
              // Crear nueva sesión
              await createSession(sessionId, null);
              console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} reconectada automáticamente`);
            }
          } else {
            console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} está conectada correctamente`);
          }
          
        } catch (sessionError) {
          console.error(`[WEBCONNECT] Error monitoreando sesión ${sessionId}:`, sessionError.message);
          
          // Si hay error, eliminar sesión y recrear
          if (sessions[sessionId]) {
            try {
              await sessions[sessionId].close();
            } catch (e) {
              // Ignorar errores
            }
            delete sessions[sessionId];
            
            // Intentar recrear
            try {
              await createSession(sessionId, null);
              console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} recreada después de error`);
            } catch (recreateError) {
              console.error(`[WEBCONNECT] ❌ Error recreando sesión ${sessionId}:`, recreateError.message);
            }
          }
        }
      }
      
    } catch (error) {
      console.error('[WEBCONNECT] Error en monitoreo de sesiones:', error);
    }
  }, 300000); // Cada 5 minutos
}

module.exports = { 
  createSession, 
  clearSession, 
  getSession, 
  sendMessage, 
  testAPIConnection,
  initializeExistingSessions,
  monitorearSesiones,
  verificarNumeroBloqueado, // Nueva función
  sessions
};
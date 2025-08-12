// Genera un QR real de WhatsApp usando @wppconnect-team/wppconnect
const wppconnect = require('@wppconnect-team/wppconnect');
const path = require('path');
const axios = require('axios'); // Asegúrate de instalarlo: npm install axios
const { Pool } = require('pg');
const fs = require('fs');
const { pool } = require('./database');
// Objeto para gestionar las instancias activas por sesión
const sessions = {};
const { markUnknownAndMaybeRecover } = require('./unknownRecovery');
// Añadir al inicio del archivo
const { sendConnectionLostAlert, sendReconnectionSuccessAlert } = require('./emailAlerts');

// Objeto para trackear fallos de reconexión por sesión
const reconnectionFailures = {};

// URL de tu API FastAPI en Render
const API_URL = process.env.API_URL || 'https://backend-agenda-2.onrender.com';
// Control de fallback automático de QR cuando una sesión restaurada queda en notLogged
const AUTO_QR_ON_NOT_LOGGED = String(process.env.AUTO_QR_ON_NOT_LOGGED || '').toLowerCase() === 'true';
const AUTO_QR_MAX_ATTEMPTS = Number.isFinite(parseInt(process.env.AUTO_QR_MAX_ATTEMPTS || '', 10)) ? parseInt(process.env.AUTO_QR_MAX_ATTEMPTS, 10) : 1;

// TTL por defecto del QR (ms) configurable por ENV
const DEFAULT_QR_TTL_MS = (() => {
  const envMs = parseInt(process.env.QR_TTL_MS || '', 10);
  return Number.isFinite(envMs) && envMs > 0 ? envMs : 5 * 60 * 1000; // 5 min
})();

// 🔒 Nuevo: bloquear cierres automáticos del browser/servicio (por defecto: false)
const ALLOW_AUTO_CLOSE = String(process.env.ALLOW_AUTO_CLOSE || 'false').toLowerCase() === 'true';

// Timers de expiración de QR por sesión
const qrExpiryTimers = {};

function scheduleQrExpiry(sessionId, ttlMs = DEFAULT_QR_TTL_MS) {
  // Limpia timer anterior
  if (qrExpiryTimers[sessionId]) {
    clearTimeout(qrExpiryTimers[sessionId]);
  }
  qrExpiryTimers[sessionId] = setTimeout(async () => {
    try {
      const { limpiarQR } = require('./qrUtils');
      await limpiarQR(pool, sessionId);
      console.log(`[WEBCONNECT] ⏲️ QR expirado y eliminado en BD para sesión ${sessionId}`);
    } catch (e) {
      console.error(`[WEBCONNECT] Error eliminando QR expirado (${sessionId}):`, e.message);
    } finally {
      delete qrExpiryTimers[sessionId];
    }
  }, ttlMs);
}

function cancelQrExpiry(sessionId, { clearDb = false } = {}) {
  if (qrExpiryTimers[sessionId]) {
    clearTimeout(qrExpiryTimers[sessionId]);
    delete qrExpiryTimers[sessionId];
  }
  if (clearDb) {
    // Limpia QR en BD cuando ya fue escaneado/conectado
    (async () => {
      try {
        const { limpiarQR } = require('./qrUtils');
        await limpiarQR(pool, sessionId);
        console.log(`[WEBCONNECT] 🧽 QR limpiado en BD tras conexión para sesión ${sessionId}`);
      } catch (e) {
        console.error(`[WEBCONNECT] Error limpiando QR en BD:`, e.message);
      }
    })();
  }
}

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
 * 🔍 NUEVA FUNCIÓN: Verificar si un cliente existe en la base de datos
 */
async function verificarClienteExisteEnBD(sessionId) {
  let client = null;
  try {
    client = await verificationPool.connect();
    const result = await client.query('SELECT id FROM tenants WHERE id = $1', [sessionId]);
    const existe = result.rows.length > 0;
    console.log(`[WEBCONNECT] 🔍 Cliente ${sessionId} ${existe ? 'EXISTE' : 'NO EXISTE'} en BD`);
    return existe;
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error verificando cliente ${sessionId} en BD:`, error);
    return false;
  } finally {
    if (client) client.release();
  }
}

/**
 * 🗑️ NUEVA FUNCIÓN: Eliminar completamente una sesión inexistente
 */
async function eliminarSesionInexistente(sessionId) {
  try {
    console.log(`[WEBCONNECT] 🗑️ Cliente ${sessionId} no existe en BD - Eliminando sesión completa...`);
    
    // 1. Cerrar y eliminar de memoria (respetando ALLOW_AUTO_CLOSE)
    if (sessions[sessionId]) {
      try {
        await safeCloseClient(sessionId);
        console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} cerrada (respetando AUTO_CLOSE)`);
      } catch (e) {
        console.error(`[WEBCONNECT] Error cerrando sesión ${sessionId}:`, e.message);
      }
      delete sessions[sessionId];
    }
    
    // 2. Limpiar directorio de tokens
    const { limpiarSesionCompleta } = require('./sessionUtils');
    await limpiarSesionCompleta(sessionId, sessions);
    
    console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} eliminada completamente (cliente no existe en BD)`);
    return true;
  } catch (error) {
    console.error(`[WEBCONNECT] Error eliminando sesión inexistente ${sessionId}:`, error);
    return false;
  }
}

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
/**
 * PASO 1: Reemplaza tu función createSession con esta versión optimizada
 * 
 * Copia y pega esta función en tu src/app/wppconnect.js
 * reemplazando la función createSession existente
 */

async function createSession(sessionId, onQR, opts = {}) {
  const sessionName = `session_${sessionId}`;
  const sessionDir = path.join(__dirname, '../../tokens', sessionName);
  const allowQR = opts.allowQR !== false;
  const wppOpts = {
    autoClose: 0,
    waitForLogin: false,
    logQR: false,
    headless: true,
    disableSpins: true,
    browserArgs: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
    catchQR: async (base64Qr, asciiQR, attempts) => {
      try {
        if (!allowQR) {
          console.log(`[WEBCONNECT] 🚫 QR bloqueado (auto) para sesión ${sessionId}. No se cerrará el navegador ni se lanzará excepción. Intento ${attempts}`);
          // Importante: NO lanzar error aquí
          return;
        }
        if (typeof onQR === 'function') {
          await onQR(base64Qr);
        }
      } catch (err) {
        console.error(`[WEBCONNECT] Error en catchQR (${sessionId}):`, err.message);
      }
    },

    statusFind: (status) => {
      console.log(`[WEBCONNECT] Estado WA-JS sesión ${sessionId}: ${status}`);
      // Nunca cerrar en UNPAIRED/NOT_LOGGED
    }
  };

  try {
    // ✅ Pre-chequeo: verificar que el cliente exista en BD antes de crear la sesión
    const existeCliente = await verificarClienteExisteEnBD(sessionId);
    if (!existeCliente) {
      console.log(`[WEBCONNECT] 🚫 Cliente ${sessionId} no existe en BD - Cancelando creación de sesión`);
      try { await eliminarSesionInexistente(sessionId); } catch (_) {}
      return null;
    }

    console.log(`[WEBCONNECT] 🚀 Creando nueva sesión ${sessionId}`);

    // Evitar creaciones concurrentes para la misma sesión
    if (sessions[sessionId] && sessions[sessionId]._creating) {
      console.log(`[WEBCONNECT] ⏳ Creación ya en curso para ${sessionId}, se omite llamada duplicada`);
      return sessions[sessionId];
    }
    if (!sessions[sessionId]) sessions[sessionId] = {};
    sessions[sessionId]._creating = true;

    // Preflight: asegurar carpeta y limpiar locks de Chrome
    try {
      const { ensureSessionFolder, limpiarSingletonLock, waitForNoSingletonLock } = require('./sessionUtils');
      await ensureSessionFolder(sessionId);
      await limpiarSingletonLock(sessionId);
      const freed = await waitForNoSingletonLock(sessionId, 20000, 500);
      if (!freed) {
        console.warn(`[WEBCONNECT] ⚠️ SingletonLock persiste antes de crear sesión ${sessionId}, se continúa con precaución`);
      } else {
        console.log(`[WEBCONNECT] ✅ Locks liberados antes de crear sesión ${sessionId}`);
      }
    } catch (preErr) {
      console.warn(`[WEBCONNECT] ⚠️ Error en preflight de locks para ${sessionId}: ${preErr.message}`);
    }
    
    // ⬅️ Asegurar misma carpeta de tokens que el resto del sistema
    const client = await wppconnect.create({
      session: sessionName,           // ahora 'session_82'
      folderNameToken: 'tokens',
      ...wppOpts
    });

    sessions[String(sessionId)] = client;

    // Listeners que antes podían cerrar el browser: ahora solo registran
    try {
      client.onStateChange((state) => {
        console.log(`[WEBCONNECT] onStateChange ${sessionId}: ${state}`);
        const S = String(state).toUpperCase();
        if (S.includes('UNPAIRED') || S.includes('NOT_LOGGED')) {
          console.warn(`[WEBCONNECT] ⚠️ Sesión ${sessionId} sin login. Manteniendo navegador abierto.`);
        }
      });

      // Si puppeteer se desconecta, registrarlo (no auto-cerrar)
      if (client?.pupPage?.browser) {
        client.pupPage.browser().on('disconnected', () => {
          console.warn(`[WEBCONNECT] ⚠️ browserClose/disconnected en sesión ${sessionId}. No se forzará cierre aquí.`);
        });
      }
    } catch (hookErr) {
      console.warn('[WEBCONNECT] Listeners opcionales no disponibles:', hookErr.message);
    }

    // Guardar la instancia en sessions
    sessions[sessionId] = client;

    // 🔥 CONFIGURACIÓN DE EVENTOS
    client.onMessage(async (message) => {
      console.log(`[WEBCONNECT] 📨 Mensaje recibido en sesión ${sessionId}:`, message.body);
      await procesarMensaje(sessionId, message, client);
    });

    client.onStateChange((state) => {
      console.log(`[WEBCONNECT] 🔄 Estado de conexión sesión ${sessionId}:`, state);
      
      if (state === 'CONNECTED') {
        console.log(`[WEBCONNECT] 🚀 Cliente ${sessionId} listo para enviar/recibir mensajes`);
        console.log(`[WEBCONNECT] 🌐 Conectado a API: ${API_URL}`);
      } else if (state === 'DISCONNECTED') {
        console.log(`[WEBCONNECT] 🔴 Cliente ${sessionId} desconectado - Verificando reconexión...`);
        
        setTimeout(async () => {
          try {
            const current = sessions[sessionId];
            if (!current) return;

            // ⏫ Doble check: validar estado real antes de reconectar
            const [isConn, currState] = await Promise.all([
              current.isConnected().catch(() => false),
              current.getConnectionState().catch(() => state)
            ]);

            if (!isConn && String(currState).toUpperCase().includes('DISCONNECTED')) {
              console.log(`[WEBCONNECT] ⚠️ Sesión ${sessionId} sigue desconectada (estado=${currState}), iniciando reconexión...`);
              try {
                const clienteExiste = await verificarClienteExisteEnBD(sessionId);
                if (clienteExiste) {
                  await reconnectSession(sessionId);
                } else {
                  await eliminarSesionInexistente(sessionId);
                }
              } catch (reconnectError) {
                console.error(`[WEBCONNECT] ❌ Error en reconexión por desconexión para sesión ${sessionId}:`, reconnectError.message);
              }
            } else {
              console.log(`[WEBCONNECT] ℹ️ Sesión ${sessionId} ya no está desconectada (estado=${currState}), se omite reconexión`);
            }
          } catch (e) {
            console.warn(`[WEBCONNECT] ⚠️ Verificación post-desconexión falló (${sessionId}): ${e.message}`);
          }
        }, 120000); // 2 minutos
      }
    });

    // Eventos adicionales
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

    console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} creada exitosamente`);
    return client;
    
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error creando sesión ${sessionId}:`, error);
    throw error;
  } finally {
    if (sessions[sessionId]) delete sessions[sessionId]._creating;
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
async function initializeExistingSessions(specificTenants = null) {
  const fs = require('fs');
  const { Pool } = require('pg');
  const tokensDir = path.join(__dirname, '../../tokens');

  // ♻️ Migración de carpetas antiguas (p.ej., '82' -> 'session_82')
  try {
    const entries = fs.readdirSync(tokensDir, { withFileTypes: true });
    for (const e of entries) {
      if (e.isDirectory() && /^\d+$/.test(e.name)) {
        const oldPath = path.join(tokensDir, e.name);
        const newPath = path.join(tokensDir, `session_${e.name}`);
        if (!fs.existsSync(newPath)) {
          fs.renameSync(oldPath, newPath);
          console.log(`[WEBCONNECT] ♻️ Migrado tokens/${e.name} -> tokens/session_${e.name}`);
        }
      }
    }
  } catch (_) {}

  try {
    console.log('[WEBCONNECT] 🚀 Iniciando restauración de sesiones...');
    
    if (!fs.existsSync(tokensDir)) {
      console.log('[WEBCONNECT] 📁 No hay directorio de tokens');
      return;
    }

    let tenantsToInit;
    
    if (specificTenants && specificTenants.length > 0) {
      tenantsToInit = specificTenants;
      console.log(`[WEBCONNECT] 🎯 Inicializando sesiones específicas: [${specificTenants.join(', ')}]`);
    } else {
      console.log('[WEBCONNECT] ⚠️ No se proporcionaron tenants específicos - No se restaurará ninguna sesión');
      return;
    }
    
    console.log(`[WEBCONNECT] 📋 Intentando restaurar ${tenantsToInit.length} sesiones...`);
    
    for (const tenantId of tenantsToInit) {
      try {
        console.log(`[WEBCONNECT] 🔄 Restaurando sesión para tenant ${tenantId}...`);
        // ✅ Chequeo previo: si no existe en BD, omitir y limpiar
        const existe = await verificarClienteExisteEnBD(tenantId);
        if (!existe) {
          console.log(`[WEBCONNECT] 🚫 Cliente ${tenantId} no existe en BD - Omitiendo y limpiando`);
          try { await eliminarSesionInexistente(tenantId); } catch (_) {}
          continue;
        }
        
        // Verificar que existe el directorio de la sesión
        const sessionDir = path.join(tokensDir, `session_${tenantId}`);
        if (!fs.existsSync(sessionDir)) {
          console.log(`[WEBCONNECT] ❌ No existe directorio para sesión ${tenantId} - Omitiendo`);
          continue;
        }
        
        // 🔧 VALIDAR INTEGRIDAD DEL DIRECTORIO DE SESIÓN
        const archivos = fs.readdirSync(sessionDir);
        if (archivos.length === 0) {
          console.log(`[WEBCONNECT] ⚠️ Directorio vacío para sesión ${tenantId} - Omitiendo`);
          continue;
        }
        
        // Crear sesión SIN QR en arranque
        const client = await createSession(tenantId, null, { allowQR: false });
        
        if (client) {
          console.log(`[WEBCONNECT] ✅ Sesión ${tenantId} restaurada exitosamente`);
          
          // 🔧 SETUP KEEP-ALIVE INMEDIATAMENTE DESPUÉS DE RESTAURAR
          setTimeout(async () => {
            try {
              await setupKeepAlive(tenantId);
              console.log(`[WEBCONNECT] 💓 Keep-alive activado para sesión ${tenantId}`);
            } catch (keepAliveError) {
              console.error(`[WEBCONNECT] ❌ Error configurando keep-alive para ${tenantId}:`, keepAliveError.message);
            }
          }, 5000);
          
        } else {
          console.log(`[WEBCONNECT] ⚠️ Sesión ${tenantId} no pudo ser restaurada`);
        }
        
        // Pausa entre restauraciones para evitar sobrecarga
        await new Promise(resolve => setTimeout(resolve, 3000)); // Aumentado a 3 segundos
        
      } catch (error) {
        console.error(`[WEBCONNECT] ❌ Error restaurando sesión ${tenantId}:`, error.message);
      }
    }
    
    // Resumen final
    const sesionesActivas = Object.keys(sessions);
    console.log(`[WEBCONNECT] 📊 Restauración completada. Sesiones activas: [${sesionesActivas.join(', ')}]`);
    
    // 🔧 VERIFICACIÓN POST-RESTAURACIÓN (Crucial para VPS restart)
    if (sesionesActivas.length > 0) {
      console.log('[WEBCONNECT] 🔍 Programando verificación post-restauración en 30 segundos...');
      setTimeout(async () => {
        console.log('[WEBCONNECT] 🔍 Ejecutando verificación post-restauración...');
        
        for (const sessionId of sesionesActivas) {
          try {
            const session = sessions[sessionId];
            if (!session) {
              console.log(`[WEBCONNECT] ⚠️ Sesión ${sessionId} no encontrada en memoria durante verificación`);
              continue;
            }
            
            // Verificar estado de conexión
            const isConnected = await session.isConnected();
            const connectionState = await session.getConnectionState();
            
            console.log(`[WEBCONNECT] 📊 Post-restauración ${sessionId}: conectado=${isConnected}, estado=${connectionState}`);
            
            // Si no está conectada, intentar reconexión (SIN QR)
            if (!isConnected || connectionState === 'DISCONNECTED') {
              console.log(`[WEBCONNECT] 🔄 Reconectando sesión ${sessionId} después de verificación...`);
              await reconnectSession(sessionId);
            }
            
          } catch (error) {
            console.error(`[WEBCONNECT] ❌ Error en verificación post-restauración ${sessionId}:`, error.message);
            
            // Si hay error, intentar reconexión
            try {
              const clienteExiste = await verificarClienteExisteEnBD(sessionId);
              if (clienteExiste) {
                console.log(`[WEBCONNECT] 🔄 Reconectando ${sessionId} por error en verificación...`);
                await reconnectSession(sessionId);
              }
            } catch (reconnectError) {
              console.error(`[WEBCONNECT] ❌ Error en reconexión de verificación para ${sessionId}:`, reconnectError.message);
            }
          }
          
          // Pausa entre verificaciones
          await new Promise(resolve => setTimeout(resolve, 2000));
        }
        
        console.log('[WEBCONNECT] ✅ Verificación post-restauración completada');
      }, 30000);
    }
    
  } catch (error) {
    console.error('[WEBCONNECT] ❌ Error en initializeExistingSessions:', error);
  }
}

/**
 * Monitorea el estado de las sesiones y reconecta automáticamente si es necesario
 */
async function monitorearSesiones() {
  console.log('[WEBCONNECT] 🔍 Iniciando monitoreo optimizado de sesiones...');
  
  // 🔧 PRIMERA EJECUCIÓN INMEDIATA DESPUÉS DE 1 MINUTO (para VPS restart)
  setTimeout(async () => {
    console.log('[WEBCONNECT] 🔍 Primera verificación de monitoreo (1 minuto después del inicio)...');
    await ejecutarMonitoreo();
  }, 60000);
  
  // 🔧 MONITOREO REGULAR CADA 3 MINUTOS
  setInterval(async () => {
    await ejecutarMonitoreo();
  }, 180000); // Cada 3 minutos
  
  console.log('[WEBCONNECT] ⏰ Monitoreo programado - Primera verificación en 1 minuto, luego cada 3 minutos');
}

// Estados que requieren re-login con QR. No cerrar ni reconectar automáticamente.
const QR_REQUIRED_STATES = new Set(['UNPAIRED', 'UNPAIRED_IDLE', 'UNPAIRED_FROM_MOBILE', 'NOT_LOGGED', 'QR']);

// Reconexión segura: no cerrar navegador si el estado requiere QR
async function reconnectSession(sessionId, reason = 'monitor') {
  const client = sessions[String(sessionId)];
  if (!client) {
    console.log(`[WEBCONNECT] ⚠️ No hay cliente en memoria para ${sessionId}`);
    return { ok: false, skipped: true, reason: 'NO_CLIENT' };
  }

  let state = 'UNKNOWN';
  try {
    state = await client.getConnectionState();
  } catch (_) {}

  if (QR_REQUIRED_STATES.has(String(state).toUpperCase())) {
    console.log(`[WEBCONNECT] ⛔ Re-conexión omitida para ${sessionId}: estado=${state} requiere QR. Manteniendo navegador abierto.`);
    return { ok: true, skipped: true, reason: 'QR_REQUIRED' };
  }

  console.log(`[WEBCONNECT] 🔄 Reconexión segura para ${sessionId} (estado=${state}, motivo=${reason})...`);

  // Intento “soft” sin cerrar el browser
  try {
    if (typeof client.restartService === 'function') {
      await client.restartService();
      console.log(`[WEBCONNECT] ✅ restartService ejecutado para ${sessionId}`);
      return { ok: true, restarted: true };
    }
  } catch (e) {
    console.warn(`[WEBCONNECT] ⚠️ restartService falló para ${sessionId}: ${e.message}`);
  }

  // ⏫ Doble check previo al fallback
  try {
    const [isConn2, state2] = await Promise.all([
      client.isConnected().catch(() => false),
      client.getConnectionState().catch(() => 'UNKNOWN')
    ]);
    if (isConn2 || String(state2).toUpperCase() === 'CONNECTED') {
      console.log(`[WEBCONNECT] ℹ️ Sesión ${sessionId} ya conectada tras revalidación (estado=${state2})`);
      return { ok: true, skipped: true, reason: 'ALREADY_CONNECTED' };
    }
  } catch (_) {}

  // Fallback: recrear sesión SOLO si está permitido cerrar automáticamente
  if (!ALLOW_AUTO_CLOSE) {
    console.log(`[WEBCONNECT] 🔒 AUTO_CLOSE deshabilitado. Omitiendo cierre/recreación para ${sessionId}`);
    return { ok: false, skipped: true, reason: 'AUTO_CLOSE_DISABLED' };
  }

  try {
    console.log(`[WEBCONNECT] 🧹 Limpiando sesión anterior para ${sessionId}`);
    await safeCloseClient(sessionId);
    console.log(`[WEBCONNECT] 🚀 Creando nueva sesión ${sessionId}`);
    await createSession(sessionId, undefined, { allowQR: false });
    console.log(`[WEBCONNECT] ✅ Reconexión completada para ${sessionId}`);
    return { ok: true, recreated: true };
  } catch (e) {
    console.error(`[WEBCONNECT] ❌ Error al reconectar ${sessionId}: ${e.message}`);
    return { ok: false, error: e.message };
  }
}

// Cierre seguro para reutilizar en reconexión (centraliza el .close() y limpieza)
async function safeCloseClient(sessionId) {
  const client = sessions[String(sessionId)];
  try {
    // Siempre limpia keep-alives
    try {
      clearKeepAlive(sessionId);
      console.log(`[WEBCONNECT] 🛑 Keep-alive intervals limpiados para ${sessionId}`);
    } catch (_) {}

    // Bloquear cierre si NO está permitido automáticamente
    if (!ALLOW_AUTO_CLOSE) {
      console.log(`[WEBCONNECT] 🔒 AUTO_CLOSE deshabilitado. No se cierra cliente ${sessionId}`);
      return;
    }

    if (client && typeof client.close === 'function') {
      await client.close();
      console.log(`[WEBCONNECT] 🔐 Cliente ${sessionId} cerrado correctamente`);
    }
  } catch (e) {
    console.warn(`[WEBCONNECT] ⚠️ Error cerrando cliente ${sessionId}: ${e.message}`);
  } finally {
    // Reemplaza liberarLocks por limpieza conocida
    try {
      const { limpiarSingletonLock } = require('./sessionUtils');
      await limpiarSingletonLock(sessionId);
    } catch (_) {}
  }
}

// ✅ Helper para limpiar intervals de keep-alive sin depender de referencias externas
function clearKeepAlive(sessionId) {
  const client = sessions[sessionId];
  if (client && Array.isArray(client._keepAliveIntervals)) {
    for (const it of client._keepAliveIntervals) {
      try { clearInterval(it); } catch (_) {}
    }
    client._keepAliveIntervals = [];
  }
}

// PASO 3B: Actualiza tu module.exports para incluir las nuevas funciones
/**
 * 🧹 NUEVA FUNCIÓN: Limpia sesiones huérfanas (sesiones sin cliente en BD)
 */
async function limpiarSesionesHuerfanas() {
  try {
    console.log('[WEBCONNECT] 🧹 Iniciando limpieza de sesiones huérfanas...');
    
    const sesionesActivas = Object.keys(sessions);
    let sesionesLimpiadas = 0;
    
    for (const sessionId of sesionesActivas) {
      const clienteExiste = await verificarClienteExisteEnBD(sessionId);
      if (!clienteExiste) {
        console.log(`[WEBCONNECT] 🗑️ Sesión huérfana detectada: ${sessionId} - Eliminando...`);
        await eliminarSesionInexistente(sessionId);
        sesionesLimpiadas++;
      }
    }
    
    console.log(`[WEBCONNECT] ✅ Limpieza completada. ${sesionesLimpiadas} sesiones huérfanas eliminadas`);
    return sesionesLimpiadas;
  } catch (error) {
    console.error('[WEBCONNECT] Error en limpieza de sesiones huérfanas:', error);
    return 0;
  }
}

/**
 * PASO 2: Agrega estas funciones nuevas al final de tu src/app/wppconnect.js
 * 
 * Copia y pega estas funciones ANTES del module.exports al final del archivo
 */

// 🔥 NUEVA FUNCIÓN: Keep-Alive avanzado para mantener sesiones vivas
async function setupKeepAlive(sessionId) {
  const client = sessions[String(sessionId)];
  if (!client) return;

  const keepAliveInterval = setInterval(async () => {
    try { await client.getConnectionState(); } catch (_) {}
  }, 90000); // 90 segundos
  
  if (!client._keepAliveIntervals) client._keepAliveIntervals = [];
  client._keepAliveIntervals.push(keepAliveInterval);
  
  console.log(`[WEBCONNECT] ✅ Keep-alive configurado para sesión ${sessionId}`);
}

// 🔥 NUEVA FUNCIÓN: Sistema de backup de sesiones autenticadas
async function saveSessionBackup(sessionId) {
  try {
    // ✅ Evitar guardar backup si el cliente ya no existe
    const existe = await verificarClienteExisteEnBD(sessionId);
    if (!existe) {
      console.log(`[WEBCONNECT] � Cliente ${sessionId} no existe en BD - No se guarda backup`);
      return false;
    }

    console.log(`[WEBCONNECT] �💾 Creando backup para sesión ${sessionId}...`);
    
    const sessionDir = path.join(__dirname, '../../tokens', `session_${sessionId}`);
    const backupDir = path.join(sessionDir, 'backup');
    
    // Crear directorio de backup si no existe
    if (!fs.existsSync(backupDir)) {
      fs.mkdirSync(backupDir, { recursive: true });
    }
    
    // Preferimos copiar el perfil completo 'Default' para preservar cookies y storage
    const criticalFiles = [
      'Default',
      'session.json'
    ];
    
    let archivosSalvados = 0;
    
    for (const file of criticalFiles) {
      const srcPath = path.join(sessionDir, file);
      const destPath = path.join(backupDir, file);
      
      if (fs.existsSync(srcPath)) {
        try {
          // Crear directorio padre si es necesario
          const destDir = path.dirname(destPath);
          if (!fs.existsSync(destDir)) {
            fs.mkdirSync(destDir, { recursive: true });
          }
          
          if (fs.statSync(srcPath).isDirectory()) {
            // Copiar directorio completo
            fs.cpSync(srcPath, destPath, { recursive: true, force: true });
          } else {
            // Copiar archivo individual
            fs.copyFileSync(srcPath, destPath);
          }
          
          archivosSalvados++;
          console.log(`[WEBCONNECT] 📁 Backup: ${file} copiado`);
          
        } catch (copyError) {
          console.log(`[WEBCONNECT] ⚠️ No se pudo copiar ${file}:`, copyError.message);
        }
      }
    }
    
    // Crear metadata del backup
    const backupMetadata = {
      sessionId: sessionId,
      timestamp: new Date().toISOString(),
      filesBackedUp: archivosSalvados,
      version: '1.0'
    };
    
    fs.writeFileSync(
      path.join(backupDir, 'backup-metadata.json'), 
      JSON.stringify(backupMetadata, null, 2)
    );
    
  console.log(`[WEBCONNECT] ✅ Backup completado para sesión ${sessionId} (${archivosSalvados} item(s))`);
    return true;
    
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error creando backup para ${sessionId}:`, error.message);
    return false;
  }
}

// 🔥 NUEVA FUNCIÓN: Reconexión inteligente
async function reconnectSession(sessionId, reason = 'monitor') {
  const client = sessions[String(sessionId)];
  if (!client) {
    console.log(`[WEBCONNECT] ⚠️ No hay cliente en memoria para ${sessionId}`);
    return { ok: false, skipped: true, reason: 'NO_CLIENT' };
  }

  let state = 'UNKNOWN';
  try {
    state = await client.getConnectionState();
  } catch (_) {}

  if (QR_REQUIRED_STATES.has(String(state).toUpperCase())) {
    console.log(`[WEBCONNECT] ⛔ Re-conexión omitida para ${sessionId}: estado=${state} requiere QR. Manteniendo navegador abierto.`);
    return { ok: true, skipped: true, reason: 'QR_REQUIRED' };
  }

  console.log(`[WEBCONNECT] 🔄 Reconexión segura para ${sessionId} (estado=${state}, motivo=${reason})...`);

  try {
    if (typeof client.restartService === 'function') {
      await client.restartService();
      console.log(`[WEBCONNECT] ✅ restartService ejecutado para ${sessionId}`);
      return { ok: true, restarted: true };
    }
  } catch (e) {
    console.warn(`[WEBCONNECT] ⚠️ restartService falló para ${sessionId}: ${e.message}`);
  }

  try {
    const [isConn2, state2] = await Promise.all([
      client.isConnected().catch(() => false),
      client.getConnectionState().catch(() => 'UNKNOWN')
    ]);
    if (isConn2 || String(state2).toUpperCase() === 'CONNECTED') {
      console.log(`[WEBCONNECT] ℹ️ Sesión ${sessionId} ya conectada tras revalidación (estado=${state2})`);
      return { ok: true, skipped: true, reason: 'ALREADY_CONNECTED' };
    }
  } catch (_) {}

  if (!ALLOW_AUTO_CLOSE) {
    console.log(`[WEBCONNECT] 🔒 AUTO_CLOSE deshabilitado. Omitiendo cierre/recreación para ${sessionId}`);
    return { ok: false, skipped: true, reason: 'AUTO_CLOSE_DISABLED' };
  }

  try {
    console.log(`[WEBCONNECT] 🧹 Limpiando sesión anterior para ${sessionId}`);
    await safeCloseClient(sessionId);
    console.log(`[WEBCONNECT] 🚀 Creando nueva sesión ${sessionId}`);
    await createSession(sessionId, undefined, { allowQR: false });
    console.log(`[WEBCONNECT] ✅ Reconexión completada para ${sessionId}`);
    return { ok: true, recreated: true };
  } catch (e) {
    console.error(`[WEBCONNECT] ❌ Error al reconectar ${sessionId}: ${e.message}`);
    return { ok: false, error: e.message };
  }
}

// ✅ Implementación faltante para evitar fallos en monitoreo
async function ejecutarMonitoreo() {
  try {
    await monitorSessions();
  } catch (err) {
    console.error('[WEBCONNECT] ❌ Error en ejecutarMonitoreo:', err?.message || err);
  }
}

// 🔥 NUEVA FUNCIÓN: ciclo de monitoreo (lo que antes faltaba)
async function monitorSessions() {
  try {
    const sessionIds = Object.keys(sessions);
    console.log(`[WEBCONNECT] 📊 Monitoreando ${sessionIds.length} sesiones: [${sessionIds.join(', ')}]`);
    if (sessionIds.length === 0) {
      console.log('[WEBCONNECT] ℹ️ No hay sesiones para monitorear');
      return;
    }

    for (const sessionId of sessionIds) {
      try {
        // Validar que el cliente exista en BD
        const existe = await verificarClienteExisteEnBD(sessionId);
        if (!existe) {
          console.log(`[WEBCONNECT] 🚫 Cliente ${sessionId} NO existe en BD - limpiando sesión`);
          await eliminarSesionInexistente(sessionId);
          continue;
        }

        const client = sessions[sessionId];
        if (!client) continue;

        console.log(`[WEBCONNECT] 🔍 Cliente ${sessionId} EXISTE en BD`);
        const isConnected = await client.isConnected().catch(() => false);
        const state = await client.getConnectionState().catch(() => 'UNKNOWN');
        console.log(`[WEBCONNECT] 📡 Sesión ${sessionId}: conectado=${isConnected}, estado=${state}`);

        // Recuperación por UNKNOWN: si supera el umbral, recrea ignorando AUTO_CLOSE
        const recovered = await markUnknownAndMaybeRecover(
          sessionId,
          { connected: isConnected, state },
          {
            maxUnknownCycles: parseInt(process.env.MONITOR_UNKNOWN_MAX_CYCLES || '3', 10),
            clearSession: async (id) => {
              try { await clearSession(id, { force: true }); } catch (_) {}
            },
            createSession: async (id) => {
              await createSession(id, null, { allowQR: false });
            },
            logger: console,
          }
        );
        if (recovered) {
          // Ya se recreó la sesión, pasar a la siguiente
          continue;
        }

        if (isConnected && String(state).toUpperCase() === 'CONNECTED') {
          // Éxito: resetear contador de fallos y notificar recuperación si aplica
          if (reconnectionFailures[sessionId]?.lost && typeof sendReconnectionSuccessAlert === 'function') {
            try { await sendReconnectionSuccessAlert(sessionId); } catch(_) {}
          }
          reconnectionFailures[sessionId] = { count: 0, lost: false };
          console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} está funcionando correctamente`);
          try {
            await client.getConnectionState();
            console.log(`[WEBCONNECT] 💚 Sesión ${sessionId} responde correctamente`);
          } catch (_) {}
        } else {
          // Desconectado: contar fallos, alertar una sola vez, reconectar sin cerrar navegador
          const entry = reconnectionFailures[sessionId] || { count: 0, lost: false };
          entry.count += 1;
          if (!entry.lost && typeof sendConnectionLostAlert === 'function') {
            try { await sendConnectionLostAlert(sessionId); } catch(_) {}
            entry.lost = true;
          }
          reconnectionFailures[sessionId] = entry;

          // Evitar acciones si requiere QR
          if (QR_REQUIRED_STATES.has(String(state).toUpperCase())) {
            console.log(`[WEBCONNECT] ⛔ ${sessionId} requiere QR. No se cierra ni reinicia el navegador.`);
          } else {
            console.log(`[WEBCONNECT] 🔄 Intentando reconectar sesión ${sessionId}...`);
            await reconnectSession(sessionId, 'monitor');
          }
        }
      } catch (perSessionErr) {
        console.error(`[WEBCONNECT] ❌ Error monitoreando sesión ${sessionId}:`, perSessionErr.message);
      }

      // Pequeño delay entre sesiones
      await new Promise(r => setTimeout(r, 500));
    }

    console.log(`[WEBCONNECT] ✅ Monitoreo completado para ${sessionIds.length} sesiones`);
  } catch (err) {
    console.error('[WEBCONNECT] ❌ Error general en monitorSessions:', err?.message || err);
  }
}

/**
 * Limpia la sesión específica y la elimina del pool de sesiones.
 * @param {string|number} sessionId
 * @param {{ force?: boolean }} opts - force:true permite cerrar aunque AUTO_CLOSE esté deshabilitado
 */
async function clearSession(sessionId, opts = {}) {
  const { force = false } = opts;
  const sessionDir = path.join(__dirname, '../../tokens', `session_${sessionId}`);
  
  try {
    console.log(`[WEBCONNECT] 🧹 Limpiando sesión ${sessionId}...`);
    
    // Limpiar intervals de keep-alive si existen
    if (sessions[sessionId] && sessions[sessionId]._keepAliveIntervals) {
      sessions[sessionId]._keepAliveIntervals.forEach(interval => {
        clearInterval(interval);
      });
      console.log(`[WEBCONNECT] 🛑 Keep-alive intervals limpiados para ${sessionId}`);
    }
    
    // Cerrar cliente si existe (respetando AUTO_CLOSE a menos que force=true)
    if (sessions[sessionId]) {
      if (ALLOW_AUTO_CLOSE || force) {
        await safeCloseClient(sessionId);
      } else {
        console.log(`[WEBCONNECT] 🔒 AUTO_CLOSE deshabilitado. No se cierra cliente ${sessionId} (use force:true para forzar).`);
      }
    }

    // Eliminar del pool en memoria
    delete sessions[sessionId];

    // Limpiar archivos de locks conocidos
    try {
      const candidates = ['SingletonLock', 'SingletonSocket', 'SingletonCookie'];
      const defaultDir = path.join(sessionDir, 'Default');
      for (const f of candidates) {
        const p1 = path.join(sessionDir, f);
        const p2 = path.join(defaultDir, f);
        try { if (fs.existsSync(p1)) fs.rmSync(p1, { force: true }); } catch (_) {}
        try { if (fs.existsSync(p2)) fs.rmSync(p2, { force: true }); } catch (_) {}
      }
    } catch (err) {
      console.error(`[WEBCONNECT] Error eliminando locks:`, err);
    }
    
    console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} limpiada completamente`);
    
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error limpiando sesión ${sessionId}:`, error);
    throw error;
  }
}

/**
 * 🔄 NUEVA FUNCIÓN: Restaurar sesión desde backup (evita ReferenceError)
 */
async function restoreFromBackup(sessionId, { overwrite = false } = {}) {
  try {
    const sessionDir = path.join(__dirname, '../../tokens', `session_${sessionId}`);
    const backupDir = path.join(sessionDir, 'backup');

    if (!fs.existsSync(backupDir)) {
      console.log(`[WEBCONNECT] ⚠️ No hay backup para sesión ${sessionId}`);
      return false;
    }

    // Crear carpeta destino si no existe
    if (!fs.existsSync(sessionDir)) {
      fs.mkdirSync(sessionDir, { recursive: true });
    }

    const items = ['Default', 'session.json'];
    let restored = 0;

    for (const it of items) {
      const src = path.join(backupDir, it);
      const dst = path.join(sessionDir, it);

      if (!fs.existsSync(src)) continue;
      if (!overwrite && fs.existsSync(dst)) {
        console.log(`[WEBCONNECT] ⏩ Saltando ${it} (existe y overwrite=false)`);
        continue;
      }

      try {
        if (fs.statSync(src).isDirectory()) {
          fs.cpSync(src, dst, { recursive: true, force: true });
        } else {
          // Asegurar dir padre
          const parent = path.dirname(dst);
          if (!fs.existsSync(parent)) fs.mkdirSync(parent, { recursive: true });
          fs.copyFileSync(src, dst);
        }
        restored++;
        console.log(`[WEBCONNECT] ♻️ Restaurado ${it} para sesión ${sessionId}`);
      } catch (e) {
        console.warn(`[WEBCONNECT] ⚠️ No se pudo restaurar ${it}: ${e.message}`);
      }
    }

    console.log(`[WEBCONNECT] ✅ RestoreFromBackup completado (${restored} item(s)) para sesión ${sessionId}`);
    return restored > 0;
  } catch (err) {
    console.error(`[WEBCONNECT] ❌ Error en restoreFromBackup(${sessionId}):`, err.message);
    return false;
  }
}

/**
 * Helpers exportados que faltaban
 */
function getSession(sessionId) {
  return sessions[String(sessionId)] || null;
}

async function isSessionActive(sessionId) {
  const client = getSession(sessionId);
  if (!client) return false;
  try {
    return await client.isConnected();
  } catch (_) {
    return false;
  }
}

async function getAllSessionsStatus() {
  const ids = Object.keys(sessions);
  const out = await Promise.all(ids.map(async (id) => {
    const client = sessions[id];
    let connected = false;
    let state = 'UNKNOWN';
    try { connected = await client.isConnected(); } catch (_) {}
    try { state = await client.getConnectionState(); } catch (_) {}
    return { id, connected, state };
  }));
  return out;
}

module.exports = { 
  createSession, 
  clearSession,
  getSession,
  isSessionActive,
  getAllSessionsStatus,
  sendMessage, 
  testAPIConnection,
  initializeExistingSessions,
  monitorearSesiones,
  ejecutarMonitoreo,
  verificarNumeroBloqueado,
  verificarClienteExisteEnBD,
  eliminarSesionInexistente,
  limpiarSesionesHuerfanas,
  setupKeepAlive,
  saveSessionBackup,
  reconnectSession,
  restoreFromBackup, // ✅ definida
  sessions,
  DEFAULT_QR_TTL_MS
};

// Evita que cualquier rechazo no manejado cierre el navegador/proceso
process.on('unhandledRejection', (reason) => {
  console.warn('[WEBCONNECT] ⚠️ Unhandled Rejection capturada (suprimida):', reason?.message || reason);
});
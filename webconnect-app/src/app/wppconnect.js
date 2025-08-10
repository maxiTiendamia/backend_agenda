// Genera un QR real de WhatsApp usando @wppconnect-team/wppconnect
const wppconnect = require('@wppconnect-team/wppconnect');
const path = require('path');
const axios = require('axios'); // Asegúrate de instalarlo: npm install axios
const { Pool } = require('pg');
const fs = require('fs');
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
    
    // 1. Cerrar y eliminar de memoria
    if (sessions[sessionId]) {
      try {
        await sessions[sessionId].close();
        console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} cerrada`);
      } catch (e) {
        console.error(`[WEBCONNECT] Error cerrando sesión ${sessionId}:`, e.message);
      }
      delete sessions[sessionId];
    }
    
    // 2. Limpiar directorio de tokens
    const { limpiarSesionCompleta } = require('./sessionUtils');
    await limpiarSesionCompleta(sessionId);
    
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

async function createSession(sessionId, onQR) {
  const sessionDir = path.join(__dirname, '../../tokens', `session_${sessionId}`);
  
  try {
    console.log(`[WEBCONNECT] 🚀 Creando nueva sesión ${sessionId}`);
    
    const client = await wppconnect.create({
      session: `session_${sessionId}`,
      folderNameToken: sessionDir,
      mkdirFolderToken: true,
      headless: true,
      devtools: false,
      useChrome: false,
      
      // 🔥 CONFIGURACIÓN OPTIMIZADA CRÍTICA
      autoClose: 0, // ¡CRÍTICO! Evita que se cierre automáticamente
      logQR: false,
      
      puppeteerOptions: {
        userDataDir: sessionDir,
        timeout: 120000, // 2 minutos para inicialización
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
          '--memory-pressure-off',
          '--max-old-space-size=512',
          
          // ✨ NUEVAS OPTIMIZACIONES PARA PERSISTENCIA
          '--disable-background-timer-throttling',
          '--disable-backgrounding-occluded-windows',
          '--disable-renderer-backgrounding',
          '--disable-background-networking',
          '--aggressive-cache-discard',
          '--disable-ipc-flooding-protection',
          `--user-data-dir=${sessionDir}`
        ]
      },
      
      catchQR: async (qrCode, asciiQR, attempts, urlCode) => {
        console.log(`[WEBCONNECT] 📱 QR generado para sesión ${sessionId}, intento ${attempts}/10`);
        
        if (attempts <= 10) {
          // Enviar QR normalmente
          if (onQR) {
            await onQR(qrCode);
          }
        } else {
          // 🔥 CRÍTICO: Detener la sesión cuando supera 10 intentos
          console.log(`[WEBCONNECT] ❌ Máximo de intentos QR alcanzado para sesión ${sessionId} - CERRANDO SESIÓN`);
          
          try {
            // 1. Marcar la sesión como fallida
            if (sessions[sessionId]) {
              sessions[sessionId]._qrFailed = true;
            }
            
            // 2. Cerrar la sesión inmediatamente
            if (sessions[sessionId]) {
              console.log(`[WEBCONNECT] 🛑 Cerrando sesión ${sessionId} por exceso de intentos QR...`);
              
              // Cerrar la instancia del cliente
              await sessions[sessionId].close();
              
              // 3. Eliminar de memoria
              delete sessions[sessionId];
              
              console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} cerrada y eliminada por exceso de intentos QR`);
            }
            
            // 4. Limpiar QR en base de datos
            try {
              const { Pool } = require('pg');
              const pool = new Pool({
                connectionString: process.env.DATABASE_URL,
                ssl: { rejectUnauthorized: false }
              });
              
              const client = await pool.connect();
              await client.query('UPDATE tenants SET qr_code = NULL WHERE id = $1', [sessionId]);
              client.release();
              await pool.end();
              
              console.log(`[WEBCONNECT] 🗑️ QR limpiado en BD para sesión ${sessionId}`);
            } catch (dbError) {
              console.error(`[WEBCONNECT] Error limpiando QR en BD:`, dbError.message);
            }
            
            // 5. Limpiar archivos de sesión para forzar nuevo QR
            const sessionDir = path.join(__dirname, '../../tokens', `session_${sessionId}`);
            if (fs.existsSync(sessionDir)) {
              fs.rmSync(sessionDir, { recursive: true, force: true });
              console.log(`[WEBCONNECT] 🗑️ Directorio de tokens eliminado para sesión ${sessionId}`);
            }
            
          } catch (closeError) {
            console.error(`[WEBCONNECT] Error cerrando sesión ${sessionId}:`, closeError.message);
          }
          
          // 6. NO procesar más QRs
          return false; // Esto detiene el procesamiento
        }
      },

      statusFind: async (statusSession, session) => {
        console.log(`[WEBCONNECT] 🔄 Estado de sesión ${sessionId}: ${statusSession}`);
        
        // 🔥 NUEVA VERIFICACIÓN: Si la sesión fue marcada como fallida, no continuar
        if (sessions[sessionId] && sessions[sessionId]._qrFailed) {
          console.log(`[WEBCONNECT] ⚠️ Sesión ${sessionId} marcada como fallida por QR - Ignorando statusFind`);
          return;
        }
        
        if (statusSession === 'qrReadSuccess') {
          console.log(`[WEBCONNECT] ✅ QR escaneado exitosamente para sesión ${sessionId}`);
          
          // Limpiar flag de fallo si existía
          if (sessions[sessionId]) {
            delete sessions[sessionId]._qrFailed;
          }
          
          // 🔥 GUARDAR BACKUP INMEDIATAMENTE
          setTimeout(async () => {
            await saveSessionBackup(sessionId);
          }, 5000);
          
        } else if (statusSession === 'isLogged') {
          console.log(`[WEBCONNECT] 📱 Sesión ${sessionId} ya está logueada - Restaurando...`);
          
        } else if (statusSession === 'connectSuccess') {
          console.log(`[WEBCONNECT] 🚀 Cliente ${sessionId} conectado y listo`);
          
          // Limpiar flag de fallo si existía
          if (sessions[sessionId]) {
            delete sessions[sessionId]._qrFailed;
          }
          
          // ✨ INICIAR KEEP-ALIVE INMEDIATAMENTE
          await setupKeepAlive(sessionId);
          
        } else if (statusSession === 'browserClose') {
          console.log(`[WEBCONNECT] 🔴 Browser cerrado para sesión ${sessionId}`);
          
          // 🔥 RECONEXIÓN INTELIGENTE solo si no falló por QR
          if (!sessions[sessionId] || !sessions[sessionId]._qrFailed) {
            setTimeout(async () => {
              try {
                const clienteExiste = await verificarClienteExisteEnBD(sessionId);
                if (clienteExiste) {
                  console.log(`[WEBCONNECT] 🔄 Iniciando reconexión automática para sesión ${sessionId}...`);
                  await reconnectSession(sessionId);
                } else {
                  console.log(`[WEBCONNECT] ❌ Cliente ${sessionId} ya no existe - Eliminando sesión`);
                  await eliminarSesionInexistente(sessionId);
                }
              } catch (error) {
                console.error(`[WEBCONNECT] Error en reconexión automática para ${sessionId}:`, error.message);
              }
            }, 3000);
          } else {
            console.log(`[WEBCONNECT] 🚫 No reconectando sesión ${sessionId} - Falló por exceso de intentos QR`);
          }
          
        } else if (statusSession === 'notLogged') {
          console.log(`[WEBCONNECT] 🔒 Sesión ${sessionId} no está logueada`);
          
        } else if (statusSession === 'qrReadFail') {
          console.log(`[WEBCONNECT] ❌ Fallo al leer QR para sesión ${sessionId}`);
          
          // 🔥 NUEVA LÓGICA: Incrementar contador de fallos
          if (!sessions[sessionId]) return;
          
          if (!sessions[sessionId]._qrFailCount) {
            sessions[sessionId]._qrFailCount = 0;
          }
          sessions[sessionId]._qrFailCount++;
          
          console.log(`[WEBCONNECT] 📊 Fallos QR para sesión ${sessionId}: ${sessions[sessionId]._qrFailCount}`);
          
          // Si hay muchos fallos consecutivos, cerrar sesión
          if (sessions[sessionId]._qrFailCount >= 3) {
            console.log(`[WEBCONNECT] ❌ Demasiados fallos QR para sesión ${sessionId} - Cerrando sesión`);
            sessions[sessionId]._qrFailed = true;
            
            try {
              await sessions[sessionId].close();
              delete sessions[sessionId];
              console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} cerrada por fallos QR consecutivos`);
            } catch (closeError) {
              console.error(`[WEBCONNECT] Error cerrando sesión por fallos QR:`, closeError.message);
            }
          }
        }
      }
    });

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
          if (sessions[sessionId] && state === 'DISCONNECTED') {
            console.log(`[WEBCONNECT] ⚠️ Sesión ${sessionId} sigue desconectada, iniciando reconexión...`);
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
        
        // Crear sesión sin callback de QR (debería usar sesión guardada)
        const client = await createSession(tenantId, null);
        
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
            
            // Si no está conectada, intentar reconexión
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

/**
 * Función auxiliar que ejecuta el monitoreo real
 */
async function ejecutarMonitoreo() {
  try {
    const sesionesActivas = Object.keys(sessions);
    
    if (sesionesActivas.length === 0) {
      console.log('[WEBCONNECT] 📊 No hay sesiones activas para monitorear');
      return;
    }
    
    console.log(`[WEBCONNECT] 📊 Monitoreando ${sesionesActivas.length} sesiones: [${sesionesActivas.join(', ')}]`);
    
    for (const sessionId of sesionesActivas) {
      try {
        // 🔍 VALIDACIÓN CRÍTICA: Verificar existencia en BD primero
        const clienteExiste = await verificarClienteExisteEnBD(sessionId);
        if (!clienteExiste) {
          console.log(`[WEBCONNECT] 🗑️ Monitoreo: Cliente ${sessionId} ya no existe en BD - Eliminando...`);
          await eliminarSesionInexistente(sessionId);
          continue;
        }
        
        const client = sessions[sessionId];
        if (!client) {
          console.log(`[WEBCONNECT] ⚠️ Sesión ${sessionId} sin cliente en memoria`);
          continue;
        }
        
        // Verificar estado de conexión con timeout
        let isConnected, connectionState;
        
        try {
          // 🔧 TIMEOUT PARA EVITAR COLGARSE
          const timeoutPromise = new Promise((_, reject) =>
            setTimeout(() => reject(new Error('Timeout verificando estado')), 10000)
          );
          
          isConnected = await Promise.race([
            client.isConnected(),
            timeoutPromise
          ]);
          
          connectionState = await Promise.race([
            client.getConnectionState(),
            timeoutPromise
          ]);
          
        } catch (stateError) {
          console.log(`[WEBCONNECT] ⚠️ Error obteniendo estado de ${sessionId}: ${stateError.message}`);
          isConnected = false;
          connectionState = 'ERROR';
        }
        
        console.log(`[WEBCONNECT] 📡 Sesión ${sessionId}: conectado=${isConnected}, estado=${connectionState}`);
        
        // 🔧 CRITERIOS MÁS AGRESIVOS PARA RECONEXIÓN
        const needsReconnection = !isConnected || 
                                 connectionState === 'DISCONNECTED' || 
                                 connectionState === 'TIMEOUT' ||
                                 connectionState === 'UNPAIRED' ||
                                 connectionState === 'ERROR' ||
                                 connectionState === 'PAIRING' ||
                                 connectionState === 'SCAN_QR_CODE';
        
        if (needsReconnection) {
          console.log(`[WEBCONNECT] 🔄 Monitoreo: Sesión ${sessionId} necesita reconexión (${connectionState})`);
          
          // Doble verificación antes de reconectar
          const clienteExisteAntesReconexion = await verificarClienteExisteEnBD(sessionId);
          if (clienteExisteAntesReconexion) {
            console.log(`[WEBCONNECT] 🚀 Iniciando reconexión para ${sessionId}...`);
            await reconnectSession(sessionId);
          } else {
            console.log(`[WEBCONNECT] ❌ Cliente ${sessionId} eliminado durante verificación`);
            await eliminarSesionInexistente(sessionId);
          }
        } else {
          console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} está funcionando correctamente`);
          
          // 🔧 VERIFICACIÓN ADICIONAL: Intentar una operación simple
          try {
            await client.getConnectionState();
            console.log(`[WEBCONNECT] 💚 Sesión ${sessionId} responde correctamente`);
          } catch (testError) {
            console.log(`[WEBCONNECT] ⚠️ Sesión ${sessionId} no responde - Programando reconexión`);
            setTimeout(async () => {
              const clienteExiste = await verificarClienteExisteEnBD(sessionId);
              if (clienteExiste) {
                await reconnectSession(sessionId);
              }
            }, 5000);
          }
        }
        
      } catch (sessionError) {
        console.error(`[WEBCONNECT] ❌ Error monitoreando sesión ${sessionId}:`, sessionError.message);
        
        // Si hay error persistente, intentar reconectar
        try {
          const clienteExiste = await verificarClienteExisteEnBD(sessionId);
          if (clienteExiste) {
            console.log(`[WEBCONNECT] 🔄 Monitoreo: Reconectando ${sessionId} debido a error`);
            await reconnectSession(sessionId);
          } else {
            await eliminarSesionInexistente(sessionId);
          }
        } catch (reconnectError) {
          console.error(`[WEBCONNECT] ❌ Error en reconexión de monitoreo para ${sessionId}:`, reconnectError.message);
        }
      }
      
      // Pequeña pausa entre verificaciones para no sobrecargar
      await new Promise(resolve => setTimeout(resolve, 2000)); // Aumentado a 2 segundos
    }
    
    console.log(`[WEBCONNECT] ✅ Monitoreo completado para ${sesionesActivas.length} sesiones`);
    
  } catch (error) {
    console.error('[WEBCONNECT] ❌ Error general en monitoreo de sesiones:', error);
  }
}

/**
 * PASO 3B: Actualiza tu module.exports para incluir las nuevas funciones
 * 
 * Reemplaza tu module.exports existente con este:
 */
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
  const client = sessions[sessionId];
  if (!client) return;
  
  console.log(`[WEBCONNECT] 💓 Configurando keep-alive para sesión ${sessionId}`);
  
  // Ping cada 45 segundos (menos frecuente para no sobrecargar)
  const keepAliveInterval = setInterval(async () => {
    try {
      // Verificar si la sesión aún existe en memoria
      if (!sessions[sessionId]) {
        console.log(`[WEBCONNECT] 🛑 Keep-alive detenido para sesión ${sessionId} (no existe en memoria)`);
        clearInterval(keepAliveInterval);
        return;
      }
      
      const isConnected = await client.isConnected();
      
      if (!isConnected) {
        console.log(`[WEBCONNECT] ⚠️ Keep-alive detectó desconexión en sesión ${sessionId}`);
        clearInterval(keepAliveInterval);
        
        // Validar cliente en BD antes de reconectar
        const clienteExiste = await verificarClienteExisteEnBD(sessionId);
        if (clienteExiste) {
          console.log(`[WEBCONNECT] 🔄 Keep-alive iniciando reconexión para ${sessionId}`);
          await reconnectSession(sessionId);
        } else {
          console.log(`[WEBCONNECT] ❌ Keep-alive: Cliente ${sessionId} no existe en BD`);
          await eliminarSesionInexistente(sessionId);
        }
      } else {
        // Operación ligera para mantener conexión activa
        try {
          await client.getConnectionState();
          console.log(`[WEBCONNECT] 💓 Keep-alive OK para sesión ${sessionId}`);
        } catch (pingError) {
          console.log(`[WEBCONNECT] ⚠️ Keep-alive ping falló para ${sessionId}:`, pingError.message);
        }
      }
      
    } catch (error) {
      console.error(`[WEBCONNECT] ❌ Error en keep-alive para ${sessionId}:`, error.message);
      
      // Si hay error persistente, reiniciar keep-alive
      clearInterval(keepAliveInterval);
      setTimeout(() => {
        setupKeepAlive(sessionId);
      }, 60000); // Reiniciar en 1 minuto
    }
  }, 45000); // 45 segundos
  
  // Guardar referencia del interval para limpieza posterior
  if (!client._keepAliveIntervals) client._keepAliveIntervals = [];
  client._keepAliveIntervals.push(keepAliveInterval);
  
  console.log(`[WEBCONNECT] ✅ Keep-alive configurado para sesión ${sessionId}`);
}

// 🔥 NUEVA FUNCIÓN: Sistema de backup de sesiones autenticadas
async function saveSessionBackup(sessionId) {
  try {
    console.log(`[WEBCONNECT] 💾 Creando backup para sesión ${sessionId}...`);
    
    const sessionDir = path.join(__dirname, '../../tokens', `session_${sessionId}`);
    const backupDir = path.join(sessionDir, 'backup');
    
    // Crear directorio de backup si no existe
    if (!fs.existsSync(backupDir)) {
      fs.mkdirSync(backupDir, { recursive: true });
    }
    
    // Archivos críticos para backup (los más importantes para mantener sesión)
    const criticalFiles = [
      'Default/Local Storage',
      'Default/Session Storage', 
      'Default/IndexedDB',
      'Default/Web Data',
      'Default/Cookies',
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
    
    console.log(`[WEBCONNECT] ✅ Backup completado para sesión ${sessionId} (${archivosSalvados} archivos)`);
    return true;
    
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error creando backup para ${sessionId}:`, error.message);
    return false;
  }
}

// 🔥 NUEVA FUNCIÓN: Reconexión inteligente
async function reconnectSession(sessionId) {
  try {
    console.log(`[WEBCONNECT] 🔄 Iniciando reconexión inteligente para ${sessionId}...`);
    
    // PASO 1: Limpiar sesión anterior
    if (sessions[sessionId]) {
      console.log(`[WEBCONNECT] 🧹 Limpiando sesión anterior para ${sessionId}`);
      
      // Limpiar intervals de keep-alive
      if (sessions[sessionId]._keepAliveIntervals) {
        sessions[sessionId]._keepAliveIntervals.forEach(interval => {
          clearInterval(interval);
        });
        console.log(`[WEBCONNECT] 🛑 Keep-alive intervals limpiados para ${sessionId}`);
      }
      
      // Cerrar cliente
      try {
        await sessions[sessionId].close();
        console.log(`[WEBCONNECT] 🔐 Cliente ${sessionId} cerrado correctamente`);
      } catch (closeError) {
        console.log(`[WEBCONNECT] ⚠️ Error cerrando cliente ${sessionId}:`, closeError.message);
      }
      
      // Eliminar de memoria
      delete sessions[sessionId];
    }
    
    // PASO 2: Esperar a que se liberen recursos
    console.log(`[WEBCONNECT] ⏳ Esperando liberación de recursos para ${sessionId}...`);
    await new Promise(resolve => setTimeout(resolve, 3000));
    
    // PASO 3: Intentar restaurar desde backup si existe
    const backupRestored = await restoreFromBackup(sessionId);
    if (backupRestored) {
      console.log(`[WEBCONNECT] 📂 Backup restaurado para ${sessionId}`);
    }
    
    // PASO 4: Crear nueva sesión
    console.log(`[WEBCONNECT] 🚀 Creando nueva sesión para ${sessionId}...`);
    await createSession(sessionId, null); // Sin callback de QR, debería usar sesión guardada
    
    console.log(`[WEBCONNECT] ✅ Reconexión completada exitosamente para ${sessionId}`);
    return true;
    
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error en reconexión para ${sessionId}:`, error.message);
    
    // Si falla, programar otro intento en 2 minutos
    console.log(`[WEBCONNECT] ⏰ Programando reintento de reconexión para ${sessionId} en 2 minutos...`);
    setTimeout(async () => {
      try {
        // Verificar nuevamente que el cliente existe antes del reintento
        const clienteExiste = await verificarClienteExisteEnBD(sessionId);
        if (clienteExiste) {
          console.log(`[WEBCONNECT] 🔄 Segundo intento de reconexión para ${sessionId}...`);
          await reconnectSession(sessionId);
        } else {
          console.log(`[WEBCONNECT] ❌ Cliente ${sessionId} eliminado - Cancelando reintento`);
          await eliminarSesionInexistente(sessionId);
        }
      } catch (retryError) {
        console.error(`[WEBCONNECT] ❌ Reintento de reconexión falló para ${sessionId}:`, retryError.message);
      }
    }, 120000); // 2 minutos
    
    return false;
  }
}

// 🔥 NUEVA FUNCIÓN: Restaurar desde backup
async function restoreFromBackup(sessionId) {
  try {
    const sessionDir = path.join(__dirname, '../../tokens', `session_${sessionId}`);
    const backupDir = path.join(sessionDir, 'backup');
    const metadataFile = path.join(backupDir, 'backup-metadata.json');
    
    // Verificar si existe backup
    if (!fs.existsSync(backupDir) || !fs.existsSync(metadataFile)) {
      console.log(`[WEBCONNECT] 📂 No hay backup disponible para ${sessionId}`);
      return false;
    }
    
    // Leer metadata del backup
    const metadata = JSON.parse(fs.readFileSync(metadataFile, 'utf8'));
    console.log(`[WEBCONNECT] 📂 Restaurando backup de ${sessionId} (${metadata.timestamp})`);
    
    // Obtener lista de archivos en backup
    const backupFiles = fs.readdirSync(backupDir).filter(file => file !== 'backup-metadata.json');
    
    let archivosRestaurados = 0;
    
    for (const file of backupFiles) {
      try {
        const srcPath = path.join(backupDir, file);
        const destPath = path.join(sessionDir, file);
        
        // Crear directorio padre si es necesario
        const destDir = path.dirname(destPath);
        if (!fs.existsSync(destDir)) {
          fs.mkdirSync(destDir, { recursive: true });
        }
        
        if (fs.statSync(srcPath).isDirectory()) {
          // Restaurar directorio completo
          fs.cpSync(srcPath, destPath, { recursive: true, force: true });
        } else {
          // Restaurar archivo individual
          fs.copyFileSync(srcPath, destPath);
        }
        
        archivosRestaurados++;
        
      } catch (restoreError) {
        console.log(`[WEBCONNECT] ⚠️ Error restaurando ${file}:`, restoreError.message);
      }
    }
    
    console.log(`[WEBCONNECT] ✅ Backup restaurado: ${archivosRestaurados} archivos para ${sessionId}`);
    return archivosRestaurados > 0;
    
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error restaurando backup para ${sessionId}:`, error.message);
    return false;
  }
}

/**
 * Limpia la sesión específica y la elimina del pool de sesiones.
 * @param {string|number} sessionId
 */
async function clearSession(sessionId) {
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
    
    // Cerrar cliente si existe
    if (sessions[sessionId]) {
      try {
        await sessions[sessionId].close();
        console.log(`[WEBCONNECT] ✅ Cliente ${sessionId} cerrado`);
      } catch (closeError) {
        console.error(`[WEBCONNECT] Error cerrando cliente ${sessionId}:`, closeError);
      }
    }

    // Eliminar del pool en memoria
    delete sessions[sessionId];

    // Limpiar archivos de sesión
    const lockFile = path.join(sessionDir, 'SingletonLock');
    try {
      if (fs.existsSync(lockFile)) {
        fs.rmSync(lockFile, { force: true });
        console.log(`[WEBCONNECT] 🗑️ SingletonLock eliminado para sesión ${sessionId}`);
      }
    } catch (err) {
      console.error(`[WEBCONNECT] Error eliminando SingletonLock:`, err);
    }
    
    console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} limpiada completamente`);
    
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error limpiando sesión ${sessionId}:`, error);
    throw error;
  }
}

/**
 * Obtiene una sesión existente del pool de sesiones
 * @param {string|number} sessionId - ID de la sesión
 * @returns {object|null} - Cliente de wppconnect o null si no existe
 */
function getSession(sessionId) {
  try {
    const client = sessions[sessionId];
    if (client) {
      console.log(`[WEBCONNECT] ✅ Sesión ${sessionId} encontrada en memoria`);
      return client;
    } else {
      console.log(`[WEBCONNECT] ⚠️ Sesión ${sessionId} no encontrada en memoria`);
      return null;
    }
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error obteniendo sesión ${sessionId}:`, error);
    return null;
  }
}

/**
 * Verifica si una sesión está activa y conectada
 * @param {string|number} sessionId - ID de la sesión
 * @returns {Promise<boolean>} - true si está conectada, false si no
 */
async function isSessionActive(sessionId) {
  try {
    const client = getSession(sessionId);
    if (!client) {
      return false;
    }
    
    const isConnected = await client.isConnected();
    console.log(`[WEBCONNECT] 📡 Sesión ${sessionId} conectada: ${isConnected}`);
    return isConnected;
  } catch (error) {
    console.error(`[WEBCONNECT] ❌ Error verificando estado de sesión ${sessionId}:`, error);
    return false;
  }
}

/**
 * Obtiene el estado de todas las sesiones activas
 * @returns {object} - Objeto con el estado de todas las sesiones
 */
async function getAllSessionsStatus() {
  const status = {};
  const sessionIds = Object.keys(sessions);
  
  console.log(`[WEBCONNECT] 📊 Obteniendo estado de ${sessionIds.length} sesiones`);
  
  for (const sessionId of sessionIds) {
    try {
      const isActive = await isSessionActive(sessionId);
      const client = sessions[sessionId];
      
      status[sessionId] = {
        active: isActive,
        hasClient: !!client,
        connected: isActive
      };
      
      if (client && isActive) {
        try {
          const connectionState = await client.getConnectionState();
          status[sessionId].connectionState = connectionState;
        } catch (stateError) {
          status[sessionId].connectionState = 'ERROR';
        }
      }
    } catch (error) {
      status[sessionId] = {
        active: false,
        hasClient: false,
        connected: false,
        error: error.message
      };
    }
  }
  
  return status;
}

module.exports = { 
  createSession, 
  clearSession,
  getSession,  // ✅ Ahora está implementada
  isSessionActive, // ✅ Nueva función auxiliar
  getAllSessionsStatus, // ✅ Nueva función para debug
  sendMessage, 
  testAPIConnection,
  initializeExistingSessions,
  monitorearSesiones,
  ejecutarMonitoreo, // ✅ Nueva función auxiliar
  verificarNumeroBloqueado,
  verificarClienteExisteEnBD,
  eliminarSesionInexistente,
  limpiarSesionesHuerfanas,
  setupKeepAlive,
  saveSessionBackup,
  reconnectSession,
  restoreFromBackup,
  sessions
};
const wppconnect = require('@wppconnect-team/wppconnect');
const redisClient = require('./redis');
const fs = require('fs');
const path = require('path');
const { getSessionFolder, cleanSessionFolder } = require('./sessionUtils');

const sessionLocks = {}; // Lock por sesión
const sessionQueues = {}; // Cola de promesas por sesión
let sessionWaitingQr = null; // sessionId que está esperando QR
const clients = {}; // Clientes activos en memoria

// Utilidades para guardar y restaurar archivos de sesión en Redis
async function saveSessionFileToRedis(sessionId, fileName) {
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
  // Buscar en la carpeta <sessionId>/fileName
  const filePath = path.join(sessionDir, String(sessionId), fileName);
  // Log de todos los archivos en la carpeta de la sesión
  try {
    const files = fs.readdirSync(path.join(sessionDir, String(sessionId)));
    console.log(`[SESSION][DISK] Archivos en la carpeta de sesión ${sessionId}: ${JSON.stringify(files)}`);
  } catch (e) {
    console.log(`[SESSION][DISK] No se pudo leer la carpeta de sesión ${sessionId}:`, e.message);
  }
  if (fs.existsSync(filePath)) {
    const data = fs.readFileSync(filePath);
    await redisClient.set(`wppconnect:${sessionId}:file:${fileName}`, data);
    console.log(`[SESSION][REDIS] Guardado ${fileName} de sesión ${sessionId} en Redis (size: ${data.length})`);
    // Opcional: eliminar el archivo local después de guardar
    fs.unlinkSync(filePath);
    // Si la carpeta queda vacía, eliminarla
    try {
      fs.rmdirSync(path.join(sessionDir, String(sessionId)));
    } catch {}
  } else {
    console.log(`[SESSION][REDIS] No existe ${fileName} para sesión ${sessionId}, no se guarda en Redis`);
  }
}

async function restoreSessionFileFromRedis(sessionId, fileName) {
  const data = await redisClient.get(`wppconnect:${sessionId}:file:${fileName}`);
  if (data) {
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, 'tokens');
    // Restaurar en la carpeta <sessionId>/fileName
    const dirPath = path.join(sessionDir, String(sessionId));
    if (!fs.existsSync(dirPath)) {
      fs.mkdirSync(dirPath, { recursive: true });
    }
    const filePath = path.join(dirPath, fileName);
    fs.writeFileSync(filePath, data);
    console.log(`[SESSION][REDIS] Restaurado ${fileName} de sesión ${sessionId} desde Redis (size: ${data.length})`);
    return true;
  } else {
    console.log(`[SESSION][REDIS] No existe ${fileName} en Redis para sesión ${sessionId}, no se restaura`);
  }
  return false;
}

// Guardar ambos archivos comunes de sesión
async function saveAllSessionFilesToRedis(sessionId) {
  await saveSessionFileToRedis(sessionId, 'tokens.json');
  await saveSessionFileToRedis(sessionId, 'sessionData.json');
}

// Restaurar ambos archivos comunes de sesión
async function restoreAllSessionFilesFromRedis(sessionId) {
  await restoreSessionFileFromRedis(sessionId, 'tokens.json');
  await restoreSessionFileFromRedis(sessionId, 'sessionData.json');
}


// Guarda el estado de la sesión en Redis
async function setSessionState(sessionId, state) {
  await redisClient.set(`wppconnect:${sessionId}:state`, state);
  console.log(`[REDIS] Estado guardado: sesión=${sessionId}, estado=${state}`);
}

// Obtiene el estado de la sesión desde Redis
async function getSessionState(sessionId) {
  const state = await redisClient.get(`wppconnect:${sessionId}:state`);
  console.log(`[REDIS] Estado consultado: sesión=${sessionId}, estado=${state}`);
  return state;
}

// Obtiene todos los sessionId logueados
async function getLoggedSessions() {
  const keys = await redisClient.keys('wppconnect:*:state');
  const sessions = [];
  console.log(`[REDIS] Claves encontradas: ${JSON.stringify(keys)}`);
  for (const key of keys) {
    const state = await redisClient.get(key);
    const sessionId = key.split(':')[1];
    console.log(`[REDIS] Estado de sesión consultado: sesión=${sessionId}, estado=${state}`);
    if (state === 'loggedIn') {
      sessions.push(sessionId);
    }
  }
  console.log(`[REDIS] Sesiones logueadas encontradas: ${JSON.stringify(sessions)}`);
  return sessions;
}

// Guarda el flag de sesión activa en Redis
async function setHasSession(sessionId, value) {
  await redisClient.set(`wppconnect:${sessionId}:hasSession`, value ? 'true' : 'false');
  console.log(`[REDIS] Flag hasSession guardado: sesión=${sessionId}, valor=${value}`);
}

// Obtiene los sessionId que tienen info previa en Redis
async function getSessionsWithInfo() {
  const keys = await redisClient.keys('wppconnect:*:hasSession');
  const sessions = [];
  for (const key of keys) {
    const value = await redisClient.get(key);
    if (value === 'true') {
      const sessionId = key.split(':')[1];
      sessions.push(sessionId);
    }
  }
  console.log(`[REDIS] Sesiones con info previa encontradas: ${JSON.stringify(sessions)}`);
  return sessions;
}

// Guarda el flag de que la sesión necesita QR en Redis
async function setNeedsQr(sessionId, value) {
  await redisClient.set(`wppconnect:${sessionId}:needsQr`, value ? 'true' : 'false');
  console.log(`[REDIS] Flag needsQr guardado: sesión=${sessionId}, valor=${value}`);
}

// Obtiene el flag de que la sesión necesita QR desde Redis
async function getNeedsQr(sessionId) {
  const value = await redisClient.get(`wppconnect:${sessionId}:needsQr`);
  console.log(`[REDIS] Flag needsQr consultado: sesión=${sessionId}, valor=${value}`);
  return value === 'true';
}

// Devuelve el estado completo de la sesión
async function getSessionStatus(sessionId) {
  const state = await getSessionState(sessionId);
  const hasSession = await redisClient.get(`wppconnect:${sessionId}:hasSession`);
  const needsQr = await getNeedsQr(sessionId);
  return {
    sessionId,
    state,
    hasSession: hasSession === 'true',
    needsQr
  };
}

// Guarda el motivo y fecha de desconexión en Redis
async function setDisconnectReason(sessionId, reason) {
  const timestamp = new Date().toISOString();
  await redisClient.set(`wppconnect:${sessionId}:lastDisconnect`, JSON.stringify({ reason, timestamp }));
  console.log(`[REDIS] Desconexión guardada: sesión=${sessionId}, motivo=${reason}, fecha=${timestamp}`);
}

async function cancelWaitingQrSession() {
  if (sessionWaitingQr) {
    const sessionId = sessionWaitingQr;
    console.log(`[QR CANCEL] Cancelando sesión esperando QR: ${sessionId}`);
    // Elimina carpeta y claves de Redis
    const folder = getSessionFolder(sessionId);
    if (fs.existsSync(folder)) {
      fs.rmSync(folder, { recursive: true, force: true });
      console.log(`[QR CANCEL] Carpeta de sesión ${sessionId} eliminada`);
    }
    const sessionKeys = await redisClient.keys(`wppconnect:${sessionId}:*`);
    for (const sk of sessionKeys) {
      await redisClient.del(sk);
      console.log(`[QR CANCEL] Clave Redis eliminada: ${sk}`);
    }
    sessionWaitingQr = null;
  }
}

// Modifica createSession para cancelar la anterior si hay QR pendiente
async function createSession(sessionId, onQr, onMessage) {
  return enqueueSessionTask(sessionId, async () => {
    if (sessionWaitingQr && sessionWaitingQr !== sessionId) {
      await cancelWaitingQrSession(); // Cancela la anterior
      // Ahora sigue con la nueva sesión normalmente
    }
    if (sessionLocks[sessionId]) {
      console.log(`[LOCK] Sesión ${sessionId} está bloqueada, pero se fuerza la creación/restauración para obtener QR.`);
      // No retornes, sigue el flujo para que se genere el QR aunque esté bloqueada
    }
    sessionLocks[sessionId] = true;
    try {
      console.log(`[DEBUG] Llamando a createSession con sessionId=${sessionId}, carpeta=${getSessionFolder(sessionId)}`);
      await cleanSessionFolder(sessionId);
      await restoreAllSessionFilesFromRedis(sessionId);

      const clientPromise = wppconnect.create({
        session: sessionId,
        folderNameToken: path.join(process.env.SESSION_FOLDER || path.join(__dirname, 'tokens'), String(sessionId)),
        catchQR: async (base64Qr, asciiQR, attempts, urlCode) => {
          sessionWaitingQr = sessionId;
          // Guarda el QR en la base en cada intento
          if (onQr) await onQr(base64Qr, sessionId);
          // Guardado solo en la base de datos
          // Guardar QR en la base de datos usando qrUtils.js (evita dependencia circular)
          try {
            const { guardarQR } = require('./qrUtils');
            await guardarQR(sessionId, base64Qr);
            console.log(`[QR][DB] Guardado QR para sesión ${sessionId} en la base de datos`);
          } catch (err) {
            console.error(`[QR][DB] Error guardando QR en la base de datos para sesión ${sessionId}:`, err);
          }
        },
        statusFind: async (statusSession, session) => {
          // Si la sesión se loguea, libera el bloqueo
          const estadosConectado = ['isLogged', 'inChat', 'CONNECTED', 'connected'];
          if (estadosConectado.includes(statusSession)) {
            sessionWaitingQr = null;
            await redisClient.del(`wppconnect:${session}:qrCode`);
            // Setear flags de sesión logueada
            await setSessionState(session, 'loggedIn');
            await setHasSession(session, true);
            await setNeedsQr(session, false);
            await setDisconnectReason(session, 'loggedIn'); // Registrar motivo de conexión
            // Esperar 6 segundos para que WPPConnect genere los archivos de sesión
            await new Promise(res => setTimeout(res, 6000));
            // Guardar archivos de sesión en Redis al estar logueado
            await saveAllSessionFilesToRedis(session);
          } else if (
            statusSession === 'desconnectedMobile' ||
            statusSession === 'notLogged' ||
            statusSession === 'disconnected' ||
            statusSession === 'browserClose' ||
            statusSession === 'qrReadError' ||
            statusSession === 'autocloseCalled'
          ) {
            sessionWaitingQr = null;
            await setSessionState(session, 'disconnected');
            await setHasSession(session, false);
            await setNeedsQr(session, true); // Marcar que necesita QR
            await setDisconnectReason(session, statusSession);
            await saveAllSessionFilesToRedis(session);
          }
        },
        storage: {
          type: 'redis',
          redisClient,
          prefix: `wppconnect:${sessionId}:`
        },
        headless: 'new', // Usar el nuevo modo headless
        useChrome: true,
        autoClose: false,
        browserSessionToken: true, // Permite perfil persistente y archivos de sesión
        browserArgs: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-accelerated-2d-canvas',
          '--no-first-run',
          '--no-zygote',
          '--disable-gpu',
          '--disable-background-timer-throttling',
          '--disable-backgrounding-occluded-windows',
          '--disable-renderer-backgrounding',
          '--disable-features=TranslateUI',
          '--disable-web-security',
          '--disable-features=VizDisplayCompositor',
          '--single-process',
          '--no-default-browser-check',
          '--disable-default-apps',
          '--disable-background-networking',
          '--disable-sync',
          '--disable-translate',
          '--disable-plugins',
          '--disable-extensions',
          '--disable-popup-blocking'
        ]
      });

      const client = await clientPromise;
      clients[sessionId] = client; // Guarda el cliente en memoria
      client.onMessage(async (message) => {
        if (onMessage) await onMessage(message, client);
      });
      client.onStateChange(async (state) => {
        console.log(`[STATE CHANGE] Sesión ${sessionId}: ${state}`);
        if (state === 'DISCONNECTED' || state === 'TIMEOUT' || state === 'CONFLICT' || state === 'UNPAIRED') {
          await saveAllSessionFilesToRedis(sessionId);
          await reconnectSession(sessionId, onQr, onMessage);
        }
      });
      return client;
    } catch (err) {
      sessionWaitingQr = null; // Libera el bloqueo si hay error
      console.error(`[ERROR] Error creando/restaurando sesión ${sessionId}:`, err);
      await setNeedsQr(sessionId, true);
      await setDisconnectReason(sessionId, err.message || 'unknown error');
      await saveAllSessionFilesToRedis(sessionId);
      throw err;
    } finally {
      sessionLocks[sessionId] = false;
    }
  });
}

// Reintenta crear la sesión si se desconecta
async function reconnectSession(sessionId, onQr, onMessage) {
  console.log(`[RECONNECT] Reintentando sesión ${sessionId}`);
  try {
    await createSession(sessionId, onQr, onMessage);
  } catch (err) {
    console.error(`[RECONNECT] Falló la reconexión de la sesión ${sessionId}:`, err);
  }
}

// Función para reconectar solo sesiones logueadas
async function reconnectLoggedSessions(onQr, onMessage) {
  const sessions = await getLoggedSessions();
  for (const sessionId of sessions) {
    await createSession(sessionId, onQr, onMessage);
  }
}

// Restaurar sesiones desde Redis (para todas las que tienen info previa)
async function reconnectSessionsWithInfo(onQr, onMessage) {
  const sessions = await getSessionsWithInfo();
  for (const sessionId of sessions) {
    await createSession(sessionId, onQr, onMessage);
  }
}

// Inicialización automática al arrancar el servicio
async function startAllSessions(onQr, onMessage) {
  // Restaurar todas las sesiones que tienen info previa
  await reconnectSessionsWithInfo(onQr, onMessage);
}

// Si ejecutas este archivo directamente, inicia las sesiones automáticamente
if (require.main === module) {
  // Puedes personalizar estos callbacks según tu lógica
  const onQr = async (base64Qr, sessionId) => {
    console.log(`QR para sesión ${sessionId}:`);
    await guardarQR(sessionId, base64Qr); 
  };
  const onMessage = (message, client) => {
    console.log(`Mensaje recibido en sesión ${client.session}:`, message);
    // Aquí tu lógica de mensajes
  };
  startAllSessions(onQr, onMessage).then(() => {
    console.log('Sesiones restauradas automáticamente desde Redis.');
  });
}

// Limpia sesiones inválidas (needsQr=true) de Redis y tokens
async function cleanInvalidSessions() {
  const keys = await redisClient.keys('wppconnect:*:needsQr');
  for (const key of keys) {
    const needsQr = await redisClient.get(key);
    if (needsQr === 'true') {
      const sessionId = key.split(':')[1];
      // Ya no se elimina carpeta de tokens, solo Redis
      // Eliminar todas las claves de la sesión en Redis
      const sessionKeys = await redisClient.keys(`wppconnect:${sessionId}:*`);
      for (const sk of sessionKeys) {
        await redisClient.del(sk);
        console.log(`[REDIS] Clave eliminada: ${sk}`);
      }
      console.log(`[CLEAN] Sesión inválida limpiada: ${sessionId}`);
    }
  }
}

async function getQrCode(sessionId) {
  return await redisClient.get(`wppconnect:${sessionId}:qrCode`);
}

// Elimina todo lo relacionado a una sesión y la reinicia
async function resetSession(sessionId, onQr, onMessage) {
  return enqueueSessionTask(sessionId, async () => {
    if (sessionLocks[sessionId]) {
      console.log(`[LOCK] Sesión ${sessionId} está bloqueada, omitiendo duplicado de reset.`);
      return;
    }
    sessionLocks[sessionId] = true;
    try {
      // 1. Eliminar carpeta de tokens
      const folder = getSessionFolder(sessionId);
      if (fs.existsSync(folder)) {
        fs.rmSync(folder, { recursive: true, force: true });
        console.log(`[RESET] Carpeta de sesión ${sessionId} eliminada`);
      }

      // 2. Eliminar todas las claves de Redis para esa sesión
      const sessionKeys = await redisClient.keys(`wppconnect:${sessionId}:*`);
      for (const sk of sessionKeys) {
        await redisClient.del(sk);
        console.log(`[RESET] Clave Redis eliminada: ${sk}`);
      }

      // 3. Cerrar cliente si existe
      if (clients[sessionId]) {
        try {
          await clients[sessionId].close();
          console.log(`[RESET] Cliente de sesión ${sessionId} cerrado`);
        } catch (e) {
          console.log(`[RESET] Error cerrando cliente de sesión ${sessionId}:`, e);
        }
        delete clients[sessionId];
      }

      // 4. Actualizar flags en Redis SIEMPRE
      await setSessionState(sessionId, 'disconnected');
      await setHasSession(sessionId, false);
      await setNeedsQr(sessionId, true);
      await setDisconnectReason(sessionId, 'reset');

      // 5. Reiniciar la sesión desde cero
      await createSession(sessionId, onQr, onMessage);
      console.log(`[RESET] Sesión ${sessionId} reiniciada desde cero`);
    } finally {
      sessionLocks[sessionId] = false;
    }
  });
}

function enqueueSessionTask(sessionId, task) {
  if (!sessionQueues[sessionId]) {
    sessionQueues[sessionId] = Promise.resolve();
  }
  // Encadena la tarea en la cola
  sessionQueues[sessionId] = sessionQueues[sessionId].then(() => task()).catch(() => {});
  return sessionQueues[sessionId];
}

module.exports = {
  createSession,
  setSessionState,
  getSessionState,
  getLoggedSessions,
  reconnectLoggedSessions,
  startAllSessions,
  setHasSession,
  getSessionsWithInfo,
  reconnectSessionsWithInfo,
  setNeedsQr,
  getNeedsQr,
  getSessionStatus,
  cleanInvalidSessions,
  resetSession,
  getQrCode
};

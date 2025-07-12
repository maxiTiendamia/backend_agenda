const wppconnect = require('@wppconnect-team/wppconnect');
const redisClient = require('./redis');

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

async function createSession(sessionId, onQr, onMessage) {
  return wppconnect.create({
    session: sessionId,
    catchQR: async (base64Qr, asciiQR, attempts, urlCode) => {
      if (onQr) await onQr(base64Qr, sessionId);
    },
    statusFind: async (statusSession, session) => {
      console.log(`Estado de la sesión ${session}: ${statusSession}`);
      const estadosConectado = ['isLogged', 'inChat', 'CONNECTED', 'connected'];
      if (estadosConectado.includes(statusSession)) {
        await setSessionState(session, 'loggedIn');
        await setHasSession(session, true); // Guardar flag de sesión activa
      } else if (
        statusSession === 'desconnectedMobile' ||
        statusSession === 'notLogged' ||
        statusSession === 'disconnected' ||
        statusSession === 'browserClose' ||
        statusSession === 'qrReadError' ||
        statusSession === 'autocloseCalled'
      ) {
        await setSessionState(session, 'disconnected');
      }
    },
    storage: {
      type: 'redis',
      redisClient,
      prefix: `wppconnect:${sessionId}:`
    },
    headless: 'new', // Usar el nuevo modo headless
    useChrome: true,
    autoClose: 180000,
    browserSessionToken: true, // Fuerza perfil temporal, sin disco local
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
  }).then(client => {
    client.onMessage(async (message) => {
      if (onMessage) await onMessage(message, client);
    });
    return client;
  });
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
  // Reconecta solo las sesiones logueadas
  await reconnectLoggedSessions(onQr, onMessage);
}

// Si ejecutas este archivo directamente, inicia las sesiones automáticamente
if (require.main === module) {
  // Puedes personalizar estos callbacks según tu lógica
  const onQr = (base64Qr, sessionId) => {
    console.log(`QR para sesión ${sessionId}:`);
    // Aquí podrías guardar el QR, enviarlo por API, etc.
  };
  const onMessage = (message, client) => {
    console.log(`Mensaje recibido en sesión ${client.session}:`, message);
    // Aquí tu lógica de mensajes
  };
  startAllSessions(onQr, onMessage).then(() => {
    console.log('Sesiones restauradas automáticamente desde Redis.');
  });
}

module.exports = { createSession, setSessionState, getSessionState, getLoggedSessions, reconnectLoggedSessions, startAllSessions, setHasSession, getSessionsWithInfo, reconnectSessionsWithInfo };

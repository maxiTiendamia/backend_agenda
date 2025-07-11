const wppconnect = require('@wppconnect-team/wppconnect');
const redisClient = require('./redis');

async function createSession(sessionId, onQr, onMessage) {
  return wppconnect.create({
    session: sessionId,
    catchQR: async (base64Qr, asciiQR, attempts, urlCode) => {
      if (onQr) await onQr(base64Qr, sessionId);
    },
    statusFind: async (statusSession, session) => {
      console.log(`Estado de la sesión ${session}: ${statusSession}`);
    },
    storage: {
      type: 'redis',
      redisClient,
      prefix: `wppconnect:${sessionId}:`
    },
    headless: true,
    useChrome: true,
    autoClose: 180000,
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

module.exports = { createSession };

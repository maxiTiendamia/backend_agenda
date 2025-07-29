

// Genera un QR real de WhatsApp usando @wppconnect-team/wppconnect
const wppconnect = require('@wppconnect-team/wppconnect');

async function createSession(sessionId, onQR) {
  // Crea una nueva sesión de WhatsApp
  wppconnect.create({
    session: sessionId,
    catchQR: async (qrCode, asciiQR, attempts, urlCode) => {
      // qrCode es el string que WhatsApp espera (base64)
      // urlCode es el string que se puede convertir a imagen QR
      if (onQR) {
        // Devuelve el QR en formato data:image/png;base64
        // El QR viene como data:image/png;base64,...
        await onQR(qrCode);
      }
    },
    headless: true,
    devtools: false,
    useChrome: false,
    browserArgs: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-accelerated-2d-canvas',
      '--no-first-run',
      '--no-zygote',
      '--single-process',
      '--disable-gpu'
    ]
  }).then((client) => {
    // Cliente listo, puedes guardar el estado si lo necesitas
    // Aquí puedes manejar eventos de mensajes, conexión, etc.
  }).catch((error) => {
    console.error('Error creando sesión WhatsApp:', error);
  });
}

module.exports = { createSession };

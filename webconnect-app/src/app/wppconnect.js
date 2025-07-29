// Genera un QR real en base64 usando la librería qrcode
const QRCode = require('qrcode');

async function createSession(sessionId, onQR) {
  // Simulación: genera un string para el QR
  const qrText = 'session-' + sessionId + '-' + Date.now();
  // Genera imagen PNG en base64
  const qrBase64 = await QRCode.toDataURL(qrText);
  if (onQR) await onQR(qrBase64);
}

module.exports = { createSession };

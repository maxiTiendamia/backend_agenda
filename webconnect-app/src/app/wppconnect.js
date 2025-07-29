// Stub para crear sesión de WhatsApp
async function createSession(sessionId, onQR) {
  // Simulación: genera un QR falso
  if (onQR) await onQR('FAKE-QR-CODE-' + sessionId);
}

module.exports = { createSession };

const pool = require('./index').pool;

// Guarda el QR en la base de datos (Postgres)
async function guardarQR(sessionId, base64Qr) {
  const qrCodeData = base64Qr.replace(/^data:image\/\w+;base64,/, "");
  await pool.query(
    'UPDATE tenants SET qr_code = $1 WHERE id = $2',
    [qrCodeData, sessionId]
  );
}

module.exports = { guardarQR };

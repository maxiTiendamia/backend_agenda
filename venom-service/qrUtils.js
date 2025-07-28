// Guarda el QR en la base de datos (Postgres)
async function guardarQR(pool, sessionId, base64Qr) {
  await pool.query(
    'UPDATE tenants SET qr_code = $1 WHERE id = $2',
    [base64Qr, sessionId]
  );
  console.log(`[QR][DB] QR actualizado para sesión ${sessionId}`);
}

module.exports = { guardarQR };

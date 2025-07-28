// Guarda el QR en la base de datos (Postgres)
async function guardarQR(pool, sessionId, base64Qr, force = false) {
  // Verifica si ya existe un QR
  const { rows } = await pool.query(
    'SELECT qr_code FROM tenants WHERE id = $1',
    [sessionId]
  );
  if (rows.length > 0 && rows[0].qr_code && !force) {
    console.log(`[QR][DB] Ya existe un QR para sesión ${sessionId}, no se guarda uno nuevo`);
    return false;
  }
  await pool.query(
    'UPDATE tenants SET qr_code = $1 WHERE id = $2',
    [base64Qr, sessionId]
  );
  console.log(`[QR][DB] QR actualizado para sesión ${sessionId}`);
  return true;
}

async function limpiarQR(pool, sessionId) {
  await pool.query(
    'UPDATE tenants SET qr_code = NULL WHERE id = $1',
    [sessionId]
  );
  console.log(`[QR][DB] QR limpiado para sesión ${sessionId}`);
}

module.exports = { guardarQR, limpiarQR };

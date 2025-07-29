// Utilidades QR
async function guardarQR(pool, sessionId, qr, activo) {
  // Simulación: guardar QR en base de datos
  await pool.query('UPDATE tenants SET qr_code = $1 WHERE id = $2', [qr, sessionId]);
}
async function limpiarQR(pool, sessionId) {
  await pool.query('UPDATE tenants SET qr_code = NULL WHERE id = $1', [sessionId]);
}
module.exports = { guardarQR, limpiarQR };

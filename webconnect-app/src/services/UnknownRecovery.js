const fs = require('fs');
const path = require('path');

// Contador de UNKNOWN por sesión y lock para evitar recreaciones concurrentes
const unknownCounters = new Map(); // sessionId -> count
const recreateLocks = new Set();   // sessionIds en recreación

function profileDirFor(sessionId) {
  return path.join(__dirname, '../../tokens', `session_${sessionId}`);
}

function removeProfileDir(sessionId, logger = console) {
  try {
    const dir = profileDirFor(sessionId);
    if (fs.existsSync(dir)) {
      fs.rmSync(dir, { recursive: true, force: true });
      logger.info?.(`[WEBCONNECT] 🗑️ Directorio de perfil eliminado: ${dir}`);
    }
  } catch (e) {
    logger.warn?.(`[WEBCONNECT] ⚠️ No se pudo eliminar directorio de perfil ${sessionId}: ${e.message}`);
  }
}

/**
 * Marca UNKNOWN y, si supera el umbral, fuerza recreación:
 * - cierra la sesión con force=true (sin depender de AUTO_CLOSE)
 * - elimina el perfil tokens/session_<id>
 * - relanza createSession con allowQR:false
 *
 * Retorna true si se disparó la recreación (para que el monitor saltee más acciones).
 */
async function markUnknownAndMaybeRecover(sessionId, status, opts) {
  const { connected, state } = status || {};
  const {
    maxUnknownCycles = 3,
    clearSession,     // async (id) => void  (debe aplicar force=true internamente)
    createSession,    // async (id) => void  (debe usar { allowQR:false })
    logger = console,
  } = opts || {};

  const S = String(state || 'UNKNOWN').toUpperCase();

  // Si está sano o no está UNKNOWN: resetear contador y salir
  if (connected || S !== 'UNKNOWN') {
    unknownCounters.set(String(sessionId), 0);
    return false;
  }

  // Incrementar contador UNKNOWN
  const prev = unknownCounters.get(String(sessionId)) || 0;
  const next = prev + 1;
  unknownCounters.set(String(sessionId), next);
  logger.warn?.(
    `[WEBCONNECT] 🟡 ${sessionId} en UNKNOWN (${next}/${maxUnknownCycles}). connected=${connected}`
  );

  if (next < maxUnknownCycles) return false;

  // Umbral alcanzado: forzar recreación (una sola a la vez por sesión)
  if (recreateLocks.has(String(sessionId))) {
    logger.info?.(`[WEBCONNECT] ⏳ Re-creación en curso para ${sessionId}, omitiendo`);
    return false;
  }

  recreateLocks.add(String(sessionId));
  try {
    logger.warn?.(
      `[WEBCONNECT] ⛑️ Forzando recreación de ${sessionId} tras ${next} ciclos UNKNOWN`
    );

    try {
      await clearSession?.(sessionId);
    } catch (e) {
      logger.warn?.(`[WEBCONNECT] ⚠️ clearSession(force) falló para ${sessionId}: ${e?.message || e}`);
    }

    removeProfileDir(sessionId, logger);

    await createSession?.(sessionId);
    logger.info?.(`[WEBCONNECT] ✅ Sesión ${sessionId} recreada exitosamente`);
    unknownCounters.set(String(sessionId), 0);
    return true;
  } catch (e) {
    logger.error?.(`[WEBCONNECT] ❌ Error recreando ${sessionId}: ${e?.message || e}`);
    return false;
  } finally {
    recreateLocks.delete(String(sessionId));
  }
}

module.exports = {
  markUnknownAndMaybeRecover,
}
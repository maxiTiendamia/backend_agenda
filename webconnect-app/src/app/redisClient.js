// Cliente Redis básico
const Redis = require('ioredis');

const redis = new Redis(process.env.REDIS_URL || 'rediss://default:AcOQAAIjcDEzOGI2OWU1MzYxZDQ0YWQ2YWU3ODJlNWNmMGY5MjIzY3AxMA@literate-toucan-50064.upstash.io:6379');

// Manejar eventos de conexión
redis.on('connect', () => {
  console.log('[REDIS] ✅ Conectado exitosamente');
});

redis.on('error', (err) => {
  console.error('[REDIS] ❌ Error de conexión:', err);
});

module.exports = redis;

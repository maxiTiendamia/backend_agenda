// Cliente Redis básico
const redis = require('redis');
const client = redis.createClient({
  url: process.env.REDIS_URL || 'redis://localhost:6379'
});
client.connect().catch(() => {});

module.exports = client;

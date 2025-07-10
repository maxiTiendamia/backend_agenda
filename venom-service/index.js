require('dotenv').config();
const express = require("express");
const { Pool } = require("pg");
const fs = require("fs");
const path = require("path");
const venom = require("venom-bot");
const axios = require("axios");
const app = express();
const PORT = process.env.PORT || 3000;
app.use(express.json());

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
});

const sessions = {};
const reconnectIntervals = {}; // Para manejar intervalos de reconexión
const sessionErrors = {}; // Para rastrear errores por sesión y evitar bucles infinitos

// Al inicio del archivo
const SESSION_FOLDER = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");

// **NUEVA FUNCIÓN: Verificar conectividad del backend**
async function verificarConectividadBackend() {
  try {
    console.log("🔍 Verificando conectividad del backend...");
    const response = await axios.get("https://backend-agenda-2.onrender.com/api/webhook", {
      timeout: 10000,
      validateStatus: function (status) {
        return status < 500; // Considerar OK cualquier status menor a 500
      }
    });
    console.log(`✅ Backend accesible - Status: ${response.status}`);
    return true;
  } catch (err) {
    console.error("❌ Error verificando conectividad del backend:", err.message);
    if (err.code === 'ECONNRESET' || err.code === 'ECONNREFUSED') {
      console.error("🔌 Error de conexión de red al backend");
    }
    return false;
  }
}

// **NUEVA FUNCIÓN: Test del webhook**
async function testWebhook(clienteId) {
  try {
    console.log(`🧪 Probando webhook para cliente ${clienteId}...`);
    const testResponse = await axios.post(
      "https://backend-agenda-2.onrender.com/api/webhook",
      {
        telefono: "123456789", // Teléfono de prueba
        mensaje: "test",
        cliente_id: clienteId
      },
      { timeout: 10000 }
    );
    
    console.log(`✅ Webhook test exitoso - Status: ${testResponse.status}`, testResponse.data);
    return true;
  } catch (err) {
    console.error(`❌ Error en test del webhook para cliente ${clienteId}:`, err.message);
    if (err.response) {
      console.error("❌ Respuesta del webhook con error:", {
        status: err.response.status,
        data: err.response.data
      });
    }
    return false;
  }
}

// Función para verificar el estado de una sesión
async function verificarEstadoSesion(clienteId) {
  try {
    if (sessions[clienteId] && sessions[clienteId].client) {
      const isConnected = await sessions[clienteId].client.isConnected();
      return isConnected;
    }
    return false;
  } catch (error) {
    console.log(`❌ Error verificando sesión ${clienteId}:`, error.message);
    return false;
  }
}

// Función para reconectar sesión automáticamente
async function reconectarSesion(clienteId) {
  console.log(`🔄 Intentando reconectar sesión ${clienteId}...`);
  
  // Verificar el estado de una sesión ANTES de intentar reconectar
  if (sessionErrors[clienteId] && sessionErrors[clienteId] >= 3) {
    console.log(`🚫 Cliente ${clienteId} bloqueado por errores (${sessionErrors[clienteId]}), cancelando reconexión automática`);
    return; // No programar más reintentos
  }
  
  // **NUEVO: Verificar si ya hay una reconexión en progreso**
  if (reconnectIntervals[clienteId]) {
    console.log(`⏳ Ya hay una reconexión en progreso para ${clienteId}, saltando...`);
    return;
  }
  
  // Marcar como en progreso
  reconnectIntervals[clienteId] = true;
  
  try {
    // Limpiar sesión anterior si existe
    if (sessions[clienteId]) {
      try {
        if (sessions[clienteId].client && typeof sessions[clienteId].client.close === 'function') {
          await sessions[clienteId].client.close();
        }
      } catch (e) {
        console.log(`⚠️ Error cerrando sesión anterior ${clienteId}:`, e.message);
      }
      delete sessions[clienteId];
    }
    
    // **NUEVO: Esperar antes de crear nueva sesión para evitar conflictos**
    console.log(`⏳ Esperando 5 segundos antes de crear nueva sesión ${clienteId}...`);
    await new Promise(resolve => setTimeout(resolve, 5000));
    
    await crearSesionConTimeout(clienteId, 45000, false); // Sin guardar QR en reconexión
    console.log(`✅ Sesión ${clienteId} reconectada exitosamente`);
  } catch (error) {
    console.log(`❌ Error reconectando sesión ${clienteId}:`, error.message);
    
    // Solo programar reintento si NO está bloqueado por errores (límite más estricto)
    if (!sessionErrors[clienteId] || sessionErrors[clienteId] < 3) {
      console.log(`⏳ Programando reintento de reconexión para ${clienteId} en 60 segundos...`);
      setTimeout(() => {
        reconectarSesion(clienteId);
      }, 60000); // Reintentar en 60 segundos (más tiempo)
    } else {
      console.log(`🚫 No se programará más reintentos para ${clienteId} (bloqueado por errores)`);
    }
  } finally {
    // Limpiar marca de progreso
    delete reconnectIntervals[clienteId];
  }
}

// Función para monitorear todas las sesiones
async function monitorearSesiones() {
  console.log(`🔍 Monitoreando ${Object.keys(sessions).length} sesiones activas...`);
  
  for (const clienteId in sessions) {
    // **NUEVO: Saltear si ya hay una reconexión en progreso**
    if (reconnectIntervals[clienteId]) {
      console.log(`⏳ Saltando monitoreo de ${clienteId} (reconexión en progreso)`);
      continue;
    }
    
    const estaConectada = await verificarEstadoSesion(clienteId);
    
    if (!estaConectada) {
      console.log(`🔍 Sesión ${clienteId} desconectada, iniciando reconexión...`);
      
      // Evitar múltiples reconexiones simultáneas
      if (!reconnectIntervals[clienteId]) {
        // **NUEVO: Verificar si el cliente está bloqueado antes de intentar reconectar**
        if (sessionErrors[clienteId] && sessionErrors[clienteId] >= 3) {
          console.log(`🚫 Cliente ${clienteId} bloqueado por errores, saltando reconexión automática`);
          continue;
        }
        
        // No esperar la reconexión para no bloquear otras sesiones
        reconectarSesion(clienteId).catch(err => {
          console.error(`❌ Error en reconexión automática ${clienteId}:`, err.message);
        });
      }
    }
  }
}

// Iniciar monitoreo cada 2 minutos
setInterval(monitorearSesiones, 120000);

pool.connect()
  .then(async (client) => {
    console.log("✅ Conexión a PostgreSQL exitosa");
    client.release();
    
    // Verificar qué clientes existen en la base de datos
    try {
      const result = await pool.query("SELECT id, comercio FROM tenants");
      console.log(`📊 Clientes encontrados en DB:`, result.rows.map(r => `${r.id}(${r.comercio})`));
    } catch (err) {
      console.error("❌ Error verificando clientes en DB:", err);
    }
  })
  .catch((err) => {
    console.error("❌ Error al conectar con la base de datos:", err);
  });

function crearSesionConTimeout(clienteId, timeoutMs = 60000, permitirGuardarQR = true) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      console.log(`⏱️ Timeout alcanzado para sesión ${clienteId} (${timeoutMs}ms)`);
      reject(new Error("⏱ Tiempo de espera agotado para crear sesión"));
    }, timeoutMs);
    
    crearSesion(clienteId, permitirGuardarQR).then((res) => {
      clearTimeout(timer);
      resolve(res);
    }).catch((err) => {
      clearTimeout(timer);
      reject(err);
    });
  });
}

async function crearSesion(clienteId, permitirGuardarQR = true) {
  const sessionId = String(clienteId);
  const sessionDir = SESSION_FOLDER;
  const qrPath = path.join(sessionDir, `${sessionId}.html`);

  console.log(`⚙️ Iniciando crearSesion para cliente ${sessionId}, permitirGuardarQR: ${permitirGuardarQR}`);

  // **NUEVO: Limpieza más agresiva de SingletonLock**
  await limpiarSingletonLock(sessionId);

  // Verificar si esta sesión está en bucle de errores (reducido a 3 intentos)
  if (sessionErrors[sessionId] && sessionErrors[sessionId] >= 3) {
    console.log(`🚫 Cliente ${sessionId} tiene demasiados errores consecutivos (${sessionErrors[sessionId]}), bloqueado por 30 minutos`);
    
    // Bloquear por 30 minutos
    setTimeout(() => {
      console.log(`🔓 Desbloqueando cliente ${sessionId} después de 30 minutos`);
      sessionErrors[sessionId] = 0;
    }, 30 * 60 * 1000);
    
    throw new Error(`Cliente ${sessionId} bloqueado por exceso de errores`);
  }

  // Si se pide regenerar QR, limpiar COMPLETAMENTE la información anterior
  if (permitirGuardarQR) {
    console.log(`🧹 Limpiando datos previos para cliente ${sessionId}...`);
    
    // Resetear contador de errores cuando se regenera QR explícitamente
    sessionErrors[sessionId] = 0;
    
    if (fs.existsSync(qrPath)) {
      fs.unlinkSync(qrPath);
      console.log(`🗑️ Archivo QR HTML eliminado: ${qrPath}`);
    }
    
    if (sessions[sessionId]) {
      try {
        await sessions[sessionId].close();
        console.log(`🔒 Sesión anterior cerrada para cliente ${sessionId}`);
      } catch (e) {
        console.log(`⚠️ Error cerrando sesión anterior para ${sessionId}:`, e.message);
      }
      delete sessions[sessionId];
    }
    
    // **NUEVO: Limpiar COMPLETAMENTE la carpeta de sesión anterior**
    const sessionPath = path.join(sessionDir, sessionId);
    const productionPath = `/app/tokens/${sessionId}`;
    
    for (const pathToClean of [sessionPath, productionPath]) {
      if (fs.existsSync(pathToClean)) {
        try {
          console.log(`🧹 Limpiando carpeta anterior: ${pathToClean}`);
          fs.rmSync(pathToClean, { recursive: true, force: true });
          console.log(`✅ Carpeta anterior eliminada: ${pathToClean}`);
        } catch (err) {
          console.error(`❌ Error limpiando carpeta ${pathToClean}:`, err.message);
        }
      }
    }
    
    // Crear carpeta nueva limpia
    await crearCarpetasAutomaticamente();
    
    try {
      const result = await pool.query("UPDATE tenants SET qr_code = NULL WHERE id = $1", [sessionId]);
      console.log(`🧹 QR limpiado en DB para cliente ${sessionId}, filas afectadas: ${result.rowCount}`);
    } catch (err) {
      console.error(`❌ Error limpiando QR en DB para cliente ${sessionId}:`, err);
    }
  }

  if (sessions[sessionId]) {
    console.log(`🟡 Sesión ya activa para ${sessionId}`);
    return sessions[sessionId];
  }

  console.log(`⚙️ Iniciando nueva sesión venom para ${sessionId}...`);
  console.log(`📁 Directorio de sesiones: ${sessionDir}`);
  console.log(`🎯 Ruta específica de esta sesión: ${path.join(sessionDir, sessionId)}`);
  
  if (!fs.existsSync(sessionDir)) {
    fs.mkdirSync(sessionDir, { recursive: true });
    console.log("📁 Carpeta 'sessions' creada");
  }

  // **CRÍTICO: Verificar que no existan otras sesiones interferentes**
  const carpetasExistentes = fs.readdirSync(sessionDir).filter(item => {
    const fullPath = path.join(sessionDir, item);
    return fs.statSync(fullPath).isDirectory() && item !== sessionId;
  });
  
  if (carpetasExistentes.length > 0) {
    console.log(`⚠️ ADVERTENCIA: Existen otras carpetas de sesión que podrían interferir:`);
    carpetasExistentes.forEach(carpeta => {
      console.log(`   - ${carpeta}`);
    });
  }

  // Variable para controlar si ya se guardó el QR (evitar múltiples guardados)
  let qrGuardado = false;

  try {
    const client = await venom.create({
      session: sessionId,
      multidevice: true,
      disableWelcome: true,
      sessionFolder: sessionDir,
      autoClose: 180000,
      useChrome: true,
      // **NUEVO: Configuración más estricta para evitar múltiples instancias**
      logQR: false,
      disableSpins: true,
      disableWelcome: true,
      updatesLog: false,
      browserArgs: [
        "--no-sandbox", 
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-accelerated-2d-canvas",
        "--no-first-run",
        "--no-zygote",
        "--disable-gpu",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=TranslateUI",
        "--disable-web-security",
        "--disable-features=VizDisplayCompositor",
        // **NUEVO: Prevenir múltiples instancias**
        "--single-process",
        "--no-default-browser-check",
        "--disable-default-apps",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--disable-plugins",
        "--disable-extensions",
        "--disable-popup-blocking"
      ],
      puppeteerOptions: { 
        headless: "new",
        args: [
          "--no-sandbox",
          "--disable-setuid-sandbox",
          "--single-process",
          "--disable-dev-shm-usage"
        ]
      },
      createPathFileToken: true,
      catchQR: async (base64Qr) => {
        // Solo procesar QR si se solicita explícitamente y no se ha guardado ya para esta sesión específica
        if (!permitirGuardarQR) {
          console.log(`ℹ️ QR generado para cliente ${sessionId} pero no se guardará (permitirGuardarQR=false)`);
          return;
        }
        
        if (qrGuardado) {
          console.log(`⚠️ QR ya fue procesado para esta sesión específica ${sessionId}, saltando...`);
          return;
        }
        
        console.log(`📱 Procesando nuevo QR para cliente ${sessionId}...`);
        
        try {
          // Guardar archivo HTML del QR
          const html = `<html><body style="display:flex;justify-content:center;align-items:center;height:100vh;"><img src="${base64Qr}" /></body></html>`;
          const qrPath = path.join(sessionDir, `${sessionId}.html`);
          fs.writeFileSync(qrPath, html);
          console.log(`📄 Archivo QR HTML guardado: ${qrPath}`);

          // Guardar QR en base de datos
          const qrCodeData = base64Qr.replace(/^data:image\/\w+;base64,/, "");
          const result = await pool.query(
            "UPDATE tenants SET qr_code = $1 WHERE id = $2",
            [qrCodeData, sessionId]
          );
          
          if (result.rowCount > 0) {
            console.log(`📬 QR guardado exitosamente en DB para cliente ${sessionId}`);
            qrGuardado = true; // Solo marcar como guardado si todo fue exitoso
          } else {
            console.error(`❌ No se pudo actualizar QR en DB para cliente ${sessionId} - Cliente no encontrado`);
          }
        } catch (err) {
          console.error(`❌ Error guardando QR para cliente ${sessionId}:`, err);
          // NO marcar como guardado si hubo error, para permitir reintento
        }
      },
    });

    sessions[sessionId] = client;

    // Manejar reconexión automática con limitador
    let reconexionIntentos = 0;
    const maxIntentos = 3;
    
    client.onStateChange(async (state) => {
      console.log(`🟠 Estado de la sesión ${sessionId}:`, state);
      
      if (state === "CONNECTED") {
        reconexionIntentos = 0; // Reset contador cuando se conecta exitosamente
        sessionErrors[sessionId] = 0; // **NUEVO: Reset contador de errores globales**
        console.log(`✅ Sesión ${sessionId} conectada exitosamente`);
        
        // **NUEVO: Verificar que el cliente puede recibir mensajes**
        try {
          const isConnected = await client.isConnected();
          const connectionState = await client.getConnectionState();
          console.log(`🔍 Estado detallado de sesión ${sessionId}:`, {
            isConnected,
            connectionState,
            canReceiveMessages: true // Asumimos que sí puede recibir mensajes si está conectado
          });
        } catch (verifyErr) {
          console.error(`❌ Error verificando capacidad de recepción de mensajes ${sessionId}:`, verifyErr.message);
        }
        
        // **NUEVO: Guardar información de sesión inmediatamente**
        try {
          await guardarInformacionSesion(sessionId, client);
        } catch (err) {
          console.error(`❌ Error guardando información de sesión para ${sessionId}:`, err.message);
        }
      }
      
      if (["CONFLICT", "UNPAIRED", "UNLAUNCHED", "DISCONNECTED"].includes(state)) {
        // **NUEVO: Incrementar contador de errores globales**
        if (!sessionErrors[sessionId]) sessionErrors[sessionId] = 0;
        sessionErrors[sessionId]++;
        
        console.log(`⚠️ Error ${sessionErrors[sessionId]}/3 para sesión ${sessionId}: ${state}`);
        
        // **NUEVO: Evitar reconexiones si ya hay una en progreso**
        if (reconnectIntervals[sessionId]) {
          console.log(`⏳ Reconexión ya en progreso para ${sessionId}, evitando duplicado`);
          return;
        }
        
        // Solo intentar reconexión automática si NO se está generando QR explícitamente
        // Y no se ha alcanzado el límite de errores globales
        if (!permitirGuardarQR && reconexionIntentos < maxIntentos && sessionErrors[sessionId] < 3) {
          reconexionIntentos++;
          console.log(`🔄 Intento ${reconexionIntentos}/${maxIntentos} de reconexión automática para ${sessionId} (error global: ${sessionErrors[sessionId]}/3)...`);
          
          // **NUEVO: Marcar como en progreso para evitar múltiples reconexiones**
          reconnectIntervals[sessionId] = true;
          
          // Esperar antes de intentar reconectar (tiempo progresivo)
          const tiempoEspera = 5000 * reconexionIntentos; // 5s, 10s, 15s
          await new Promise(resolve => setTimeout(resolve, tiempoEspera));
          
          try {
            // Cerrar sesión actual antes de recrear
            if (sessions[sessionId]) {
              await sessions[sessionId].close();
              delete sessions[sessionId];
            }
            
            // **NUEVO: Limpiar SingletonLock antes de recrear**
            await limpiarSingletonLock(sessionId);
            await new Promise(resolve => setTimeout(resolve, 1000)); // Espera 1 segundo
            await crearSesion(sessionId, false); // false = NO generar QR en reconexión automática
            console.log(`✅ Sesión ${sessionId} reconectada automáticamente en intento ${reconexionIntentos}`);
          } catch (err) {
            console.error(`❌ Error en intento ${reconexionIntentos} de reconexión automática ${sessionId}:`, err.message);
            
            if (reconexionIntentos >= maxIntentos || sessionErrors[sessionId] >= 3) {
              console.error(`🚫 Máximo de intentos automáticos alcanzado para ${sessionId}, bloqueando por 30 minutos`);
              
              // Bloquear temporalmente esta sesión
              setTimeout(() => {
                console.log(`🔓 Desbloqueando sesión ${sessionId} después de 30 minutos`);
                sessionErrors[sessionId] = 0;
                reconexionIntentos = 0;
              }, 30 * 60 * 1000);
            }
          } finally {
            // **NUEVO: Limpiar marca de progreso**
            delete reconnectIntervals[sessionId];
          }
        } else if (permitirGuardarQR) {
          console.log(`🔍 Sesión ${sessionId} desconectada pero está en proceso de generación de QR, no se reintenta automáticamente`);
        } else {
          console.error(`🚫 Sesión ${sessionId} desconectada permanentemente (errores: ${sessionErrors[sessionId]}/3), requiere escaneo manual de QR`);
        }
      }
    });

    client.onMessage(async (message) => {
      try {
        console.log(`📩 Mensaje recibido en cliente ${sessionId}:`, {
          from: message.from,
          body: message.body,
          type: message.type,
          timestamp: new Date().toISOString()
        });
        
        const telefono = message.from.replace("@c.us", "");
        const mensaje = message.body;
        const cliente_id = sessionId;

        console.log(`🔄 Enviando al backend - Cliente: ${cliente_id}, Teléfono: ${telefono}, Mensaje: "${mensaje}"`);

        // Envía el mensaje al backend y espera la respuesta
        const backendResponse = await axios.post(
          "https://backend-agenda-2.onrender.com/api/webhook",
          {
            telefono,
            mensaje,
            cliente_id
          }
        );

        console.log(`🔗 Respuesta del backend:`, {
          status: backendResponse.status,
          data: backendResponse.data,
          headers: backendResponse.headers['content-type']
        });

        // El backend debe responder con { mensaje: "texto a enviar" }
        const respuesta = backendResponse.data && backendResponse.data.mensaje;
        if (respuesta) {
          console.log(`💬 Enviando respuesta a ${telefono}: "${respuesta}"`);
          await client.sendText(`${telefono}@c.us`, respuesta);
          console.log(`✅ Respuesta enviada exitosamente a ${telefono}`);
        } else {
          console.log(`⚠️ Backend no devolvió mensaje para enviar. Respuesta completa:`, backendResponse.data);
        }
      } catch (err) {
        console.error("❌ Error reenviando mensaje a backend o enviando respuesta:", err);
        if (err.response) {
          console.error("❌ Respuesta del backend con error:", {
            status: err.response.status,
            data: err.response.data,
            headers: err.response.headers
          });
        }
        if (err.request) {
          console.error("❌ Error de red/conexión:", err.request);
        }
      }
    });

    console.log(`✅ Cliente venom creado para ${sessionId}`);
    
    // **NUEVO: Verificar conectividad del backend antes de finalizar**
    const backendOk = await verificarConectividadBackend();
    if (!backendOk) {
      console.error(`⚠️ ADVERTENCIA: Backend no accesible para cliente ${sessionId}. Los mensajes pueden no procesarse.`);
    } else {
      // Test del webhook si el backend está accesible
      await testWebhook(sessionId);
    }
    
    // Si se solicitó guardar QR, verificar que se haya generado después de un tiempo
    if (permitirGuardarQR) {
      console.log(`⏳ Esperando generación de QR para cliente ${sessionId}...`);
      
      // Esperar hasta 15 segundos para que se genere el QR
      let tiempoEspera = 0;
      const maxEspera = 15000; // 15 segundos
      const intervalo = 1000; // 1 segundo
      
      while (tiempoEspera < maxEspera && !qrGuardado) {
        await new Promise(resolve => setTimeout(resolve, intervalo));
        tiempoEspera += intervalo;
        
        // Verificar si el archivo QR existe
        if (fs.existsSync(qrPath)) {
          console.log(`📱 Archivo QR detectado para cliente ${sessionId} después de ${tiempoEspera}ms`);
          break;
        }
      }
      
      // Verificar estado final del QR
      if (qrGuardado) {
        console.log(`✅ QR generado y guardado exitosamente para cliente ${sessionId}`);
      } else if (fs.existsSync(qrPath)) {
        console.log(`⚠️ Archivo QR existe pero no se confirmó guardado en DB para cliente ${sessionId}`);
      } else {
        console.error(`❌ No se pudo generar QR para cliente ${sessionId} después de ${maxEspera}ms`);
      }
    }

    return client;
  } catch (err) {
    console.error(`❌ Error creando sesión para ${sessionId}:`, err.message);
    console.error(`🔍 Tipo de error:`, err.name || 'Unknown');
    
    // Log detallado del error para debugging
    if (err.stack) {
      console.error(`📋 Stack trace:`, err.stack.split('\n').slice(0, 5).join('\n'));
    }
    
    // Incrementar contador de errores para esta sesión
    sessionErrors[sessionId] = (sessionErrors[sessionId] || 0) + 1;
    console.log(`📊 Errores acumulados para cliente ${sessionId}: ${sessionErrors[sessionId]}/3`);
    
    // Limpiar sesión de memoria si existe (evitar estados inconsistentes)
    if (sessions[sessionId]) {
      try {
        console.log(`🧹 Limpiando sesión en memoria para cliente ${sessionId}...`);
        await sessions[sessionId].close();
      } catch (e) {
        console.log(`⚠️ Error cerrando sesión fallida para ${sessionId}:`, e.message);
      }
      delete sessions[sessionId];
    }
    
    // Si hay demasiados errores consecutivos, limpiar completamente y bloquear temporalmente
    if (sessionErrors[sessionId] >= 3) {
      console.log(`🚫 Cliente ${sessionId} bloqueado temporalmente por exceso de errores (${sessionErrors[sessionId]}/3)`);
      
      // Limpiar carpeta de sesión si está corrupta
      const sessionPath = path.join(sessionDir, sessionId);
      if (fs.existsSync(sessionPath)) {
        try {
          console.log(`🧹 Eliminando carpeta de sesión corrupta: ${sessionPath}`);
          fs.rmSync(sessionPath, { recursive: true, force: true });
          console.log(`✅ Carpeta eliminada para cliente ${sessionId}`);
        } catch (cleanupErr) {
          console.error(`❌ Error limpiando carpeta para ${sessionId}:`, cleanupErr.message);
        }
      }
      
      // Programar reset del contador de errores en 30 minutos
      setTimeout(() => {
        console.log(`� Desbloqueando y reseteando contador de errores para cliente ${sessionId}`);
        sessionErrors[sessionId] = 0;
      }, 30 * 60 * 1000); // 30 minutos
    }
    
    throw err;
  }
}

// Función para restaurar sesiones solo desde sessionDir
async function restaurarSesiones() {
  try {
    console.log("🔄 Iniciando restauración de sesiones...");
    
    // **NUEVO: Crear carpetas automáticamente ANTES de restaurar**
    await crearCarpetasAutomaticamente();
    
    // Verificar cuáles clientes existen en la base de datos
    let result;
    try {
      result = await pool.query("SELECT id, comercio FROM tenants ORDER BY id");
      console.log(`📊 Consultando base de datos... Encontrados ${result.rows.length} clientes`);
      if (result.rows.length > 0) {
        console.log(`👥 Clientes en DB:`, result.rows.map(r => `${r.id}(${r.comercio || 'Sin comercio'})`));
      } else {
        console.log("⚠️ No se encontraron clientes en la base de datos");
        return;
      }
    } catch (err) {
      console.error("❌ Error consultando clientes de la base de datos:", err);
      return;
    }
    
    const clientesActivos = result.rows.map(row => String(row.id));
    
    // Buscar carpetas de sesión existentes SOLO en sessionDir
    if (!fs.existsSync(SESSION_FOLDER)) {
      console.log("📁 No existe carpeta de sesiones, creándola...");
      fs.mkdirSync(SESSION_FOLDER, { recursive: true });
    }

    let sessionFolders = [];
    try {
      const localFolders = fs.readdirSync(SESSION_FOLDER).filter(item => {
        const itemPath = path.join(SESSION_FOLDER, item);
        return fs.statSync(itemPath).isDirectory() && !isNaN(item) && clientesActivos.includes(item);
      });
      console.log(`📂 Encontradas ${localFolders.length} carpetas válidas en ${SESSION_FOLDER}:`, localFolders);

      sessionFolders = localFolders.map(folder => ({
        id: folder,
        path: path.join(SESSION_FOLDER, folder)
      }));
    } catch (err) {
      console.error("❌ Error leyendo carpeta local:", err.message);
    }
    
    console.log(`🔍 Clientes activos en BD (strings): [${clientesActivos.join(', ')}]`);
    
    // Verificar estado de sesiones activas en memoria
    const sesionesEnMemoria = Object.keys(sessions);
    console.log(`💾 Sesiones activas en memoria: [${sesionesEnMemoria.join(', ')}]`);
    
    // Análisis detallado de carpetas vs clientes
    const carpetasIds = sessionFolders.map(f => f.id);
    const clientesConCarpeta = clientesActivos.filter(id => carpetasIds.includes(id));
    const clientesSinCarpeta = clientesActivos.filter(id => !carpetasIds.includes(id));
    const carpetasHuerfanas = carpetasIds.filter(id => !clientesActivos.includes(id));
    
    console.log(`📋 ANÁLISIS COMPLETO:`);
    console.log(`  - Clientes con carpeta: [${clientesConCarpeta.join(', ')}]`);
    console.log(`  - Clientes sin carpeta: [${clientesSinCarpeta.join(', ')}]`);
    console.log(`  - Carpetas huérfanas: [${carpetasHuerfanas.join(', ')}]`);
    
    for (const sessionFolder of sessionFolders) {
      const clienteId = typeof sessionFolder === 'string' ? sessionFolder : sessionFolder.id;
      const sessionPath = typeof sessionFolder === 'string' ? 
        path.join(SESSION_FOLDER, sessionFolder) : sessionFolder.path;
      
      console.log(`\n🔄 Procesando cliente ${clienteId}...`);
      
      // Solo restaurar si el cliente existe en la base de datos
      if (!clientesActivos.includes(clienteId)) {
        console.log(`⚠️ Cliente ${clienteId} no existe en DB (Clientes válidos: ${clientesActivos.join(', ')}), saltando...`);
        continue;
      }

      // Verificar si existe el archivo de datos de WhatsApp Web
      const defaultPath = path.join(sessionPath, "Default");
      
      console.log(`🔍 Verificando archivos para cliente ${clienteId}:`);
      console.log(`  - Ruta sesión: ${sessionPath}`);
      console.log(`  - Carpeta Default: ${fs.existsSync(defaultPath) ? '✅' : '❌'}`);
      
      // Verificar archivos críticos de WhatsApp Web
      let tieneArchivosDeSession = false;
      let archivosEsenciales = [];
      let archivosEncontrados = [];
      let archivosNoEncontrados = [];
      
      if (fs.existsSync(defaultPath)) {
        const archivosDefault = fs.readdirSync(defaultPath);
        console.log(`  - Archivos en Default: [${archivosDefault.join(', ')}]`);
        
        // Verificar archivos críticos específicos para WhatsApp Web
        archivosEsenciales = [
          'Local Storage',
          'Session Storage', 
          'IndexedDB',
          'Preferences'
        ];
        
        // Verificar cada archivo crítico
        for (const archivo of archivosEsenciales) {
          const existe = archivosDefault.some(archivoReal => 
            archivoReal.toLowerCase().includes(archivo.toLowerCase())
          );
          if (existe) {
            archivosEncontrados.push(archivo);
          } else {
            archivosNoEncontrados.push(archivo);
          }
          console.log(`  - ${archivo}: ${existe ? '✅' : '❌'}`);
        }
        
        // Solo considerar válida si tiene Local Storage (mínimo crítico) y al menos un archivo adicional
        const tieneLocalStorage = archivosEncontrados.some(a => a.includes('Local Storage'));
        const tienePreferences = archivosEncontrados.some(a => a.includes('Preferences'));
        const tieneIndexedDB = archivosEncontrados.some(a => a.includes('IndexedDB'));
        const tieneSessionStorage = archivosEncontrados.some(a => a.includes('Session Storage'));
        
        // Criterio MÁS ESTRICTO: requiere Local Storage + (Preferences O IndexedDB)
        tieneArchivosDeSession = tieneLocalStorage && (tienePreferences || tieneIndexedDB);
        
        if (!tieneArchivosDeSession) {
          console.log(`  - ❌ Sesión INCOMPLETA para cliente ${clienteId}:`);
          console.log(`    - Local Storage: ${tieneLocalStorage ? '✅' : '❌'}`);
          console.log(`    - Preferences: ${tienePreferences ? '✅' : '❌'}`);
          console.log(`    - IndexedDB: ${tieneIndexedDB ? '✅' : '❌'}`);
          console.log(`    - Session Storage: ${tieneSessionStorage ? '✅' : '❌'}`);
          console.log(`    - Archivos encontrados: [${archivosEncontrados.join(', ')}]`);
          console.log(`    - Archivos faltantes: [${archivosNoEncontrados.join(', ')}]`);
          console.log(`    - 🔄 Requiere re-autenticación con QR`);
        } else {
          console.log(`  - ✅ Sesión VÁLIDA para cliente ${clienteId} (Local Storage + archivos adicionales)`);
        }
      }
      
      console.log(`  - Tiene sesión restaurable: ${tieneArchivosDeSession ? '✅' : '❌'}`);
      
      if (fs.existsSync(defaultPath) && tieneArchivosDeSession) {
        console.log(`🔄 Restaurando sesión para cliente ${clienteId}...`);
        console.log(`📁 Usando ruta: ${sessionPath}`);
        try {
          // Configurar la variable de entorno para esta sesión específica
          const originalSessionFolder = process.env.SESSION_FOLDER;
          process.env.SESSION_FOLDER = path.dirname(sessionPath);
          
          // LIMPIEZA DE SINGLETONLOCK ANTES DE RESTAURAR SESIÓN
          await limpiarSingletonLock(clienteId);
          await new Promise(resolve => setTimeout(resolve, 1000)); // Espera 1 segundo

          await crearSesion(clienteId, false); // false = no regenerar QR
          console.log(`✅ Sesión restaurada para cliente ${clienteId}`);
          
          // Restaurar configuración original
          if (originalSessionFolder) {
            process.env.SESSION_FOLDER = originalSessionFolder;
          } else {
            delete process.env.SESSION_FOLDER;
          }
          
          // Esperar un poco entre restauraciones para no sobrecargar
          await new Promise(resolve => setTimeout(resolve, 3000));
        } catch (err) {
          console.error(`❌ Error restaurando sesión ${clienteId}:`, err.message);
          console.error(`🔍 Stack trace:`, err.stack);
        }
      } else {
        console.log(`⚠️ No hay datos de sesión válidos para cliente ${clienteId} en ${sessionPath}`);
        console.log(`🔍 Razones posibles:`);
        console.log(`   - Carpeta Default existe: ${fs.existsSync(defaultPath)}`);
        console.log(`   - Tiene archivos de sesión: ${tieneArchivosDeSession || 'false'}`);
        if (fs.existsSync(sessionPath)) {
          console.log(`   - Archivos en carpeta raíz: [${fs.readdirSync(sessionPath).join(', ')}]`);
        } else {
          console.log(`   - Carpeta de sesión no existe`);
        }
      }
    }
    
    console.log("✅ Proceso de restauración completado");
    
    // **NUEVA FUNCIONALIDAD: Limpiar carpetas huérfanas y SingletonLocks**
    console.log("🧹 Limpiando carpetas huérfanas y archivos de bloqueo...");
    
    // Buscar y eliminar carpetas de clientes que ya no están en la BD
    const searchPaths = [SESSION_FOLDER];
    
    for (const searchPath of searchPaths) {
      if (!fs.existsSync(searchPath)) continue;
      
      try {
        const allFolders = fs.readdirSync(searchPath).filter(item => {
          const itemPath = path.join(searchPath, item);
          return fs.statSync(itemPath).isDirectory() && !isNaN(item); // Solo carpetas numéricas
        });
        
        const huerfanas = allFolders.filter(folder => !clientesActivos.includes(folder));
        
        if (huerfanas.length > 0) {
          console.log(`🗑️ Eliminando ${huerfanas.length} carpetas huérfanas de ${searchPath}:`, huerfanas);
          
          for (const huerfana of huerfanas) {
            const carpetaPath = path.join(searchPath, huerfana);
            try {
              // Eliminar archivos SingletonLock específicamente antes de eliminar la carpeta
              const singletonLockPath = path.join(carpetaPath, "SingletonLock");
              if (fs.existsSync(singletonLockPath)) {
                fs.unlinkSync(singletonLockPath);
                console.log(`🔓 Eliminado SingletonLock de cliente ${huerfana}`);
              }
              
              // Eliminar toda la carpeta
              fs.rmSync(carpetaPath, { recursive: true, force: true });
              console.log(`✅ Carpeta huérfana eliminada: ${huerfana}`);
            } catch (err) {
              console.error(`❌ Error eliminando carpeta ${huerfana}:`, err.message);
            }
          }
        } else {
          console.log(`✅ No hay carpetas huérfanas en ${searchPath}`);
        }
      } catch (err) {
        console.error(`❌ Error durante limpieza en ${searchPath}:`, err.message);
      }
    }
    
    // Limpiar SingletonLocks de clientes activos también (por si acaso)
    for (const clienteId of clientesActivos) {
      for (const searchPath of searchPaths) {
        const singletonPath = path.join(searchPath, clienteId, "SingletonLock");
        if (fs.existsSync(singletonPath)) {
          try {
            fs.unlinkSync(singletonLockPath);
            console.log(`🔓 Limpiado SingletonLock para cliente activo ${clienteId}`);
          } catch (err) {
            console.error(`❌ Error limpiando SingletonLock ${clienteId}:`, err.message);
          }
        }
      }
    }
    
    // Mostrar clientes activos que NO tienen carpetas de sesión (solo informativo)
    console.log("🔍 Verificando clientes activos sin carpetas de sesión...");
    const carpetasExistentes = sessionFolders.map(sf => typeof sf === 'string' ? sf : sf.id);
    const clientesSinCarpetas = clientesActivos.filter(id => !carpetasExistentes.includes(id));
    
    if (clientesSinCarpetas.length > 0) {
      console.log(`📋 Clientes sin carpetas de sesión (requieren QR manual):`, clientesSinCarpetas);
      console.log(`📱 Para generar QR manualmente, usa los endpoints:`);
      clientesSinCarpetas.forEach(id => {
        console.log(`  - GET /iniciar/${id} - para generar QR`);
        console.log(`  - GET /qr/${id} - para ver QR`);
      });
    } else {
      console.log("✅ Todos los clientes activos tienen carpetas de sesión");
    }
  } catch (err) {
    console.error("❌ Error restaurando sesiones previas:", err);
  }
}

// Función para crear carpetas base automáticamente si no existen
async function crearCarpetasAutomaticamente() {
  const sessionDir = SESSION_FOLDER;
  if (!fs.existsSync(sessionDir)) {
    fs.mkdirSync(sessionDir, { recursive: true });
    console.log("📁 Carpeta de sesiones creada automáticamente:", sessionDir);
  }
}

// Limpieza agresiva de archivos SingletonLock antes de crear/restaurar sesión
async function limpiarSingletonLock(sessionId) {
  const sessionDirs = [
    SESSION_FOLDER,
    "/app/sessions",
    "/app/tokens"
  ];
  for (const dir of sessionDirs) {
    const singletonLockPath = path.join(dir, sessionId, "SingletonLock");
    if (fs.existsSync(singletonLockPath)) {
      try {
        fs.unlinkSync(singletonLockPath);
        console.log(`🔓 SingletonLock eliminado en ${singletonLockPath} para cliente ${sessionId}`);
      } catch (err) {
        console.error(`❌ Error eliminando SingletonLock en ${singletonLockPath} para ${sessionId}:`, err.message);
      }
    }
  }
}

// Guardar información esencial de sesión para restauración futura
async function guardarInformacionSesion(sessionId, client) {
  try {
    const info = await client.getHostDevice();
    const sessionDir = SESSION_FOLDER;
    const infoPath = path.join(sessionDir, sessionId, "session_info.json");
    fs.writeFileSync(infoPath, JSON.stringify(info, null, 2));
    console.log(`💾 Información de sesión guardada para cliente ${sessionId}`);
  } catch (err) {
    console.error(`❌ Error guardando información de sesión para ${sessionId}:`, err.message);
  }
}

// Inicializar la aplicación: restaurar sesiones previas
async function inicializarAplicacion() {
  try {
    await restaurarSesiones();
    console.log("🚀 Inicialización completa");
  } catch (err) {
    console.error("❌ Error durante la inicialización:", err);
  }
}

// Endpoints para limpieza y reparación
app.get('/limpiar/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  try {
    await limpiarSingletonLock(clienteId);
    res.json({ ok: true, mensaje: `SingletonLock limpiado para cliente ${clienteId}` });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.get('/restaurar', async (req, res) => {
  try {
    await restaurarSesiones();
    res.json({ ok: true, mensaje: "Sesiones restauradas" });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});


app.get('/sesiones', async (req, res) => {
  try {
    print(`🔍 Consultando estado de todas las sesiones...`);
    const result = await pool.query("SELECT id, comercio FROM tenants ORDER BY id");
    const clientes = result.rows.map(row => String(row.id));
    const estados = await Promise.all(clientes.map(async id => {
      let estado = "desconocido";
      if (sessions[id] && sessions[id].isConnected) {
        try {
          estado = (await sessions[id].isConnected()) ? "conectada" : "desconectada";
        } catch {
          estado = "error";
        }
      } else {
        estado = "sin_sesion";
      }
      return { id, comercio: row.comercio, estado };
    }));
    res.json(estados);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/reiniciar/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  try {
    await crearSesionConTimeout(clienteId, 60000, true); // true = regenerar QR
    res.json({ ok: true, mensaje: `Sesión reiniciada y QR regenerado para cliente ${clienteId}` });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.get('/qr/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  const sessionDir = SESSION_FOLDER;
  const qrPath = path.join(sessionDir, `${clienteId}.html`);
  if (fs.existsSync(qrPath)) {
    res.sendFile(qrPath);
  } else {
    res.status(404).send("QR no encontrado");
  }
});

// Endpoint compatible con admin: estado de todas las sesiones
app.get('/estado-sesiones', async (req, res) => {
  try {
    const result = await pool.query("SELECT id, nombre, comercio FROM tenants ORDER BY id");
    const clientes = result.rows;
    const estados = await Promise.all(clientes.map(async row => {
      const id = String(row.id);
      let estado = "NO_INICIADA";
      let enMemoria = false;
      let tieneArchivos = false;
      if (sessions[id] && sessions[id].getConnectionState) {
        try {
          estado = await sessions[id].getConnectionState();
          enMemoria = true;
        } catch {
          estado = "ERROR";
        }
      }
      // Verifica si hay archivos de sesión en disco
      const sessionDir = SESSION_FOLDER;
      const sessionPath = path.join(sessionDir, id);
      tieneArchivos = fs.existsSync(sessionPath);
      return {
        clienteId: id,
        nombre: row.nombre,
        comercio: row.comercio,
        estado,
        enMemoria,
        tieneArchivos
      };
    }));
    res.json(estados);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Endpoint compatible con admin: regenerar QR manualmente
app.post('/generar-qr/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  try {
    await crearSesionConTimeout(clienteId, 60000, true); // true = regenerar QR
    res.json({ ok: true, mensaje: `QR regenerado para cliente ${clienteId}` });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// Endpoint compatible con admin: resetear contador de errores
app.post('/reset-errores/:clienteId', async (req, res) => {
  const clienteId = req.params.clienteId;
  sessionErrors[clienteId] = 0;
  res.json({ ok: true, mensaje: `Errores reseteados para cliente ${clienteId}` });
});

// Endpoint para debug de errores de sesión
app.get('/debug/errores', (req, res) => {
  // Devuelve el estado actual de los errores de sesión y reconexiones
  res.json({
    sessionErrors,
    reconnectIntervals: Object.keys(reconnectIntervals),
    sesionesEnMemoria: Object.keys(sessions),
    timestamp: new Date().toISOString()
  });
});

app.get('/debug/listar-sesiones', (req, res) => {
  const sessionDir = SESSION_FOLDER;
  let resultado = {};
  try {
    if (fs.existsSync(sessionDir)) {
      const carpetas = fs.readdirSync(sessionDir);
      resultado.carpetas = carpetas.map(carpeta => {
        const carpetaPath = path.join(sessionDir, carpeta);
        let archivos = [];
        if (fs.statSync(carpetaPath).isDirectory()) {
          archivos = fs.readdirSync(carpetaPath);
        }
        return {
          carpeta,
          archivos
        };
      });
    } else {
      resultado.error = `La carpeta ${sessionDir} no existe`;
    }
  } catch (err) {
    resultado.error = err.message;
  }
  res.json(resultado);
});

// Iniciar el servidor con manejo de errores
const server = app.listen(PORT)
  .on('listening', async () => {
    console.log(`✅ Venom-service corriendo en puerto ${PORT}`);
    await inicializarAplicacion();
  })
  .on('error', (error) => {
    if (error.code === 'EADDRINUSE') {
      console.error(`❌ Puerto ${PORT} ya está en uso. Intentando puerto alternativo...`);
      const server2 = app.listen(0)
        .on('listening', async () => {
          const actualPort = server2.address().port;
          console.log(`✅ Venom-service corriendo en puerto alternativo ${actualPort}`);
          await inicializarAplicacion();
        })
        .on('error', (err) => {
          console.error(`❌ Error fatal iniciando servidor:`, err);
          process.exit(1);
        });
    } else {
      console.error(`❌ Error iniciando servidor:`, error);
      process.exit(1);
    }
  });


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
  
  // Verificar si está bloqueado por errores ANTES de intentar reconectar
  if (sessionErrors[clienteId] && sessionErrors[clienteId] >= 5) {
    console.log(`🚫 Cliente ${clienteId} bloqueado por errores (${sessionErrors[clienteId]}), cancelando reconexión automática`);
    return; // No programar más reintentos
  }
  
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
  
  try {
    await crearSesionConTimeout(clienteId, 45000, false); // Sin guardar QR en reconexión
    console.log(`✅ Sesión ${clienteId} reconectada exitosamente`);
  } catch (error) {
    console.log(`❌ Error reconectando sesión ${clienteId}:`, error.message);
    
    // Solo programar reintento si NO está bloqueado por errores
    if (!sessionErrors[clienteId] || sessionErrors[clienteId] < 5) {
      console.log(`⏳ Programando reintento de reconexión para ${clienteId} en 30 segundos...`);
      setTimeout(() => {
        reconectarSesion(clienteId);
      }, 30000); // Reintentar en 30 segundos
    } else {
      console.log(`🚫 No se programará más reintentos para ${clienteId} (bloqueado por errores)`);
    }
  }
}

// Función para monitorear todas las sesiones
async function monitorearSesiones() {
  for (const clienteId in sessions) {
    const estaConectada = await verificarEstadoSesion(clienteId);
    
    if (!estaConectada) {
      console.log(`🔍 Sesión ${clienteId} desconectada, iniciando reconexión...`);
      
      // Evitar múltiples reconexiones simultáneas
      if (!reconnectIntervals[clienteId]) {
        reconnectIntervals[clienteId] = true;
        await reconectarSesion(clienteId);
        delete reconnectIntervals[clienteId];
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
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
  const qrPath = path.join(sessionDir, `${sessionId}.html`);

  console.log(`⚙️ Iniciando crearSesion para cliente ${sessionId}, permitirGuardarQR: ${permitirGuardarQR}`);

  // Verificar si esta sesión está en bucle de errores
  if (sessionErrors[sessionId] && sessionErrors[sessionId] > 5) {
    console.log(`🚫 Cliente ${sessionId} tiene demasiados errores consecutivos (${sessionErrors[sessionId]}), saltando...`);
    throw new Error(`Cliente ${sessionId} bloqueado por exceso de errores`);
  }

  // Si se pide regenerar QR, borra el archivo, la sesión en memoria y el campo en la base
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
    
    // Limpiar archivos de bloqueo de Chrome (SingletonLock)
    const sessionPath = path.join(sessionDir, sessionId);
    const singletonLockPath = path.join(sessionPath, "SingletonLock");
    if (fs.existsSync(singletonLockPath)) {
      try {
        fs.unlinkSync(singletonLockPath);
        console.log(`🔓 Archivo SingletonLock eliminado para cliente ${sessionId}`);
      } catch (e) {
        console.log(`⚠️ Error eliminando SingletonLock: ${e.message}`);
      }
    }
    
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
  if (!fs.existsSync(sessionDir)) {
    fs.mkdirSync(sessionDir);
    console.log("📁 Carpeta 'sessions' creada");
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
        "--disable-renderer-backgrounding"
      ],
      puppeteerOptions: { 
        headless: "new",
        args: [
          "--no-sandbox",
          "--disable-setuid-sandbox"
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
        console.log(`✅ Sesión ${sessionId} conectada exitosamente`);
      }
      
      if (["CONFLICT", "UNPAIRED", "UNLAUNCHED", "DISCONNECTED"].includes(state)) {
        // Solo intentar reconexión automática si NO se está generando QR explícitamente
        if (!permitirGuardarQR && reconexionIntentos < maxIntentos) {
          reconexionIntentos++;
          console.log(`🔄 Intento ${reconexionIntentos}/${maxIntentos} de reconexión automática para ${sessionId}...`);
          
          // Esperar antes de intentar reconectar
          await new Promise(resolve => setTimeout(resolve, 5000));
          
          try {
            // Cerrar sesión actual antes de recrear
            if (sessions[sessionId]) {
              await sessions[sessionId].close();
              delete sessions[sessionId];
            }
            
            await crearSesion(sessionId, false); // false = NO generar QR en reconexión automática
            console.log(`✅ Sesión ${sessionId} reconectada automáticamente en intento ${reconexionIntentos}`);
          } catch (err) {
            console.error(`❌ Error en intento ${reconexionIntentos} de reconexión automática ${sessionId}:`, err.message);
            
            if (reconexionIntentos >= maxIntentos) {
              console.error(`🚫 Máximo de intentos automáticos alcanzado para ${sessionId}, requiere intervención manual`);
            }
          }
        } else if (permitirGuardarQR) {
          console.log(`🔍 Sesión ${sessionId} desconectada pero está en proceso de generación de QR, no se reintenta automáticamente`);
        } else {
          console.error(`🚫 Sesión ${sessionId} desconectada permanentemente, requiere escaneo manual de QR`);
        }
      }
    });

    client.onMessage(async (message) => {
      try {
        const telefono = message.from.replace("@c.us", "");
        const mensaje = message.body;
        const cliente_id = sessionId;

        // Envía el mensaje al backend y espera la respuesta
        const backendResponse = await axios.post(
          "https://backend-agenda-2.onrender.com/api/webhook",
          {
            telefono,
            mensaje,
            cliente_id
          }
        );

        // El backend debe responder con { mensaje: "texto a enviar" }
        const respuesta = backendResponse.data && backendResponse.data.mensaje;
        if (respuesta) {
          await client.sendText(`${telefono}@c.us`, respuesta);
        }
      } catch (err) {
        console.error("❌ Error reenviando mensaje a backend o enviando respuesta:", err);
      }
    });

    console.log(`✅ Cliente venom creado para ${sessionId}`);
    
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
    console.error(`❌ Error creando sesión para ${sessionId}:`, err);
    
    // Incrementar contador de errores para esta sesión
    sessionErrors[sessionId] = (sessionErrors[sessionId] || 0) + 1;
    console.log(`📊 Errores acumulados para cliente ${sessionId}: ${sessionErrors[sessionId]}`);
    
    // Si hay demasiados errores consecutivos, limpiar y bloquear temporalmente
    if (sessionErrors[sessionId] >= 5) {
      console.log(`🚫 Cliente ${sessionId} bloqueado temporalmente por exceso de errores`);
      
      // Limpiar sesión de memoria si existe
      if (sessions[sessionId]) {
        try {
          await sessions[sessionId].close();
        } catch (e) {
          // Ignorar errores al cerrar
        }
        delete sessions[sessionId];
      }
      
      // Programar reset del contador de errores en 10 minutos
      setTimeout(() => {
        console.log(`🔄 Reseteando contador de errores para cliente ${sessionId}`);
        sessionErrors[sessionId] = 0;
      }, 10 * 60 * 1000); // 10 minutos
    }
    
    throw err;
  }
}

async function verificarEstadoSesiones() {
  console.log("🔍 Verificando estado de todas las sesiones...");
  
  for (const [sessionId, client] of Object.entries(sessions)) {
    try {
      const estado = await client.getConnectionState();
      console.log(`📊 Sesión ${sessionId}: ${estado}`);
      
      if (estado !== "CONNECTED") {
        console.log(`⚠️ Sesión ${sessionId} no conectada, intentando reconectar...`);
        try {
          await client.close();
          delete sessions[sessionId];
          await crearSesion(sessionId, false);
        } catch (err) {
          console.error(`❌ Error reconectando ${sessionId}:`, err.message);
        }
      }
    } catch (err) {
      console.error(`❌ Error verificando estado de ${sessionId}:`, err.message);
      delete sessions[sessionId];
    }
  }
}

// Verificar sesiones cada 10 minutos (deshabilitado temporalmente)
// setInterval(verificarEstadoSesiones, 10 * 60 * 1000);

async function restaurarSesiones() {
  try {
    console.log("🔄 Iniciando restauración de sesiones...");
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    
    // Verificar cuáles clientes existen en la base de datos PRIMERO
    let result;
    try {
      result = await pool.query("SELECT id, comercio FROM tenants WHERE activo = true ORDER BY id");
      console.log(`📊 Consultando base de datos... Encontrados ${result.rows.length} clientes activos`);
      if (result.rows.length > 0) {
        console.log(`👥 Clientes activos en DB:`, result.rows.map(r => `${r.id}(${r.comercio || 'Sin comercio'})`));
      } else {
        console.log("⚠️ No se encontraron clientes activos en la base de datos");
        return;
      }
    } catch (err) {
      console.error("❌ Error consultando clientes de la base de datos:", err);
      // Si falla la consulta con 'activo', intentar sin esa columna
      try {
        result = await pool.query("SELECT id, comercio FROM tenants ORDER BY id");
        console.log(`📊 Consultando base de datos (sin filtro activo)... Encontrados ${result.rows.length} clientes`);
      } catch (err2) {
        console.error("❌ Error en consulta alternativa:", err2);
        return;
      }
    }
    
    const clientesActivos = result.rows.map(row => String(row.id));
    
    // Buscar carpetas de sesión existentes
    if (!fs.existsSync(sessionDir)) {
      console.log("📁 No existe carpeta de sesiones, creándola...");
      fs.mkdirSync(sessionDir, { recursive: true });
    }

    let sessionFolders = [];
    
    // Priorizar /app/tokens si existe (para producción)
    if (fs.existsSync("/app/tokens")) {
      console.log("🔍 Buscando carpetas en /app/tokens...");
      try {
        const appTokenFolders = fs.readdirSync("/app/tokens").filter(item => {
          const itemPath = path.join("/app/tokens", item);
          return fs.statSync(itemPath).isDirectory() && !isNaN(item) && clientesActivos.includes(item);
        });
        console.log(`📂 Encontradas ${appTokenFolders.length} carpetas válidas en /app/tokens:`, appTokenFolders);
        
        sessionFolders = appTokenFolders.map(folder => ({
          id: folder,
          path: path.join("/app/tokens", folder)
        }));
      } catch (err) {
        console.error("❌ Error leyendo /app/tokens:", err.message);
      }
    }
    
    // Si no hay carpetas en /app/tokens, buscar en sessionDir local
    if (sessionFolders.length === 0) {
      try {
        const localFolders = fs.readdirSync(sessionDir).filter(item => {
          const itemPath = path.join(sessionDir, item);
          return fs.statSync(itemPath).isDirectory() && !isNaN(item) && clientesActivos.includes(item);
        });
        console.log(`📂 Encontradas ${localFolders.length} carpetas válidas en ${sessionDir}:`, localFolders);
        
        sessionFolders = localFolders.map(folder => ({
          id: folder,
          path: path.join(sessionDir, folder)
        }));
      } catch (err) {
        console.error("❌ Error leyendo carpeta local:", err.message);
      }
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
    
    // Verificar específicamente el cliente 35
    if (clientesActivos.includes('35')) {
      console.log(`🔍 DIAGNÓSTICO CLIENTE 35:`);
      console.log(`  - Existe en BD: ✅`);
      console.log(`  - Tiene carpeta en disco: ${carpetasIds.includes('35') ? '✅' : '❌'}`);
      console.log(`  - Sesión en memoria: ${sesionesEnMemoria.includes('35') ? '✅' : '❌'}`);
      
      if (sessions['35']) {
        try {
          const estado35 = await sessions['35'].getConnectionState();
          console.log(`  - Estado actual: ${estado35}`);
        } catch (e) {
          console.log(`  - Error verificando estado: ${e.message}`);
        }
      }
      
      // Buscar carpeta 35 manualmente en ambas ubicaciones
      const paths35 = [
        path.join(sessionDir, '35'),
        '/app/tokens/35'
      ];
      
      for (const pathToCheck of paths35) {
        console.log(`  - Verificando ruta ${pathToCheck}: ${fs.existsSync(pathToCheck) ? '✅' : '❌'}`);
        if (fs.existsSync(pathToCheck)) {
          const defaultPath = path.join(pathToCheck, 'Default');
          console.log(`    - Default folder: ${fs.existsSync(defaultPath) ? '✅' : '❌'}`);
          if (fs.existsSync(pathToCheck)) {
            const files = fs.readdirSync(pathToCheck);
            console.log(`    - Archivos en carpeta: [${files.join(', ')}]`);
          }
        }
      }
    }
    
    for (const sessionFolder of sessionFolders) {
      const clienteId = typeof sessionFolder === 'string' ? sessionFolder : sessionFolder.id;
      const sessionPath = typeof sessionFolder === 'string' ? 
        path.join(sessionDir, sessionFolder) : sessionFolder.path;
      
      // Solo restaurar si el cliente existe en la base de datos
      if (!clientesActivos.includes(clienteId)) {
        console.log(`⚠️ Cliente ${clienteId} no existe en DB (Clientes válidos: ${clientesActivos.join(', ')}), saltando...`);
        continue;
      }

      // Verificar si existe el archivo de datos de WhatsApp Web
      const defaultPath = path.join(sessionPath, "Default");
      const whatsappDataFile = path.join(sessionPath, "Default", "Local Storage");
      
      console.log(`🔍 Verificando archivos para cliente ${clienteId}:`);
      console.log(`  - Ruta sesión: ${sessionPath}`);
      console.log(`  - Carpeta Default: ${fs.existsSync(defaultPath) ? '✅' : '❌'}`);
      console.log(`  - Local Storage: ${fs.existsSync(whatsappDataFile) ? '✅' : '❌'}`);
      
      if (fs.existsSync(defaultPath) || fs.existsSync(whatsappDataFile)) {
        console.log(`🔄 Restaurando sesión para cliente ${clienteId}...`);
        console.log(`📁 Usando ruta: ${sessionPath}`);
        try {
          // Configurar la variable de entorno para esta sesión específica
          const originalSessionFolder = process.env.SESSION_FOLDER;
          process.env.SESSION_FOLDER = path.dirname(sessionPath);
          
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
        console.log(`🔍 Archivos en la carpeta:`, fs.existsSync(sessionPath) ? fs.readdirSync(sessionPath) : "Carpeta no existe");
      }
    }
    
    console.log("✅ Proceso de restauración completado");
    
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

app.get("/iniciar/:clienteId", async (req, res) => {
  const { clienteId } = req.params;
  try {
    console.log(`🚀 Iniciando sesión para cliente ${clienteId}...`);
    await crearSesionConTimeout(clienteId, 60000, true); // true para guardar QR
    
    // Verificar que el QR se haya generado
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const qrPath = path.join(sessionDir, `${clienteId}.html`);
    
    if (fs.existsSync(qrPath)) {
      console.log(`✅ QR generado para cliente ${clienteId}`);
      res.send(`✅ Sesión iniciada para ${clienteId}. Escaneá el QR en /qr/${clienteId}`);
    } else {
      console.log(`⚠️ Sesión creada pero QR no encontrado para cliente ${clienteId}`);
      res.send(`⚠️ Sesión iniciada para ${clienteId}, pero QR aún no disponible. Reintenta en /qr/${clienteId} en unos segundos.`);
    }
  } catch (error) {
    console.error(`❌ Error al iniciar sesión para cliente ${clienteId}:`, error);
    res.status(500).send(`Error al iniciar sesión: ${error.message}`);
  }
});

app.get("/qr/:clienteId", (req, res) => {
  const clienteId = req.params.clienteId;
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
  const filePath = path.join(sessionDir, `${clienteId}.html`);
  if (fs.existsSync(filePath)) {
    res.sendFile(filePath);
  } else {
    res.status(404).send(`<h2>⚠️ Aún no se generó un QR para el cliente: ${clienteId}</h2>`);
  }
});

app.get("/estado", async (req, res) => {
  try {
    // Obtener clientes de la DB
    const result = await pool.query("SELECT id, comercio FROM tenants");
    const clientesDB = result.rows.map(r => ({ id: String(r.id), comercio: r.comercio }));
    
    // Obtener carpetas existentes
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const carpetas = fs.existsSync(sessionDir) ? 
      fs.readdirSync(sessionDir).filter(item => {
        const itemPath = path.join(sessionDir, item);
        return fs.statSync(itemPath).isDirectory() && !isNaN(item);
      }) : [];
    
    // Estado de sesiones activas
    const sesionesActivas = Object.keys(sessions);
    
    // Análisis
    const clientesConCarpeta = clientesDB.filter(c => carpetas.includes(c.id));
    const clientesSinCarpeta = clientesDB.filter(c => !carpetas.includes(c.id));
    const carpetasHuerfanas = carpetas.filter(c => !clientesDB.find(cl => cl.id === c));
    
    res.json({
      timestamp: new Date().toISOString(),
      clientes_en_db: clientesDB.length,
      carpetas_en_disco: carpetas.length,
      sesiones_activas: sesionesActivas.length,
      clientes_db: clientesDB,
      carpetas_existentes: carpetas,
      sesiones_activas_ids: sesionesActivas,
      analisis: {
        clientes_con_carpeta: clientesConCarpeta,
        clientes_sin_carpeta: clientesSinCarpeta,
        carpetas_huerfanas: carpetasHuerfanas
      }
    });
  } catch (err) {
    console.error("❌ Error obteniendo estado:", err);
    res.status(500).json({ error: "Error obteniendo estado" });
  }
});

app.post("/enviar-mensaje", async (req, res) => {
  const { cliente_id, telefono, mensaje } = req.body;
  const session = sessions[String(cliente_id)];
  if (!session) return res.status(404).json({ error: "Sesión no encontrada para este cliente" });

  try {
    const state = await session.getConnectionState();
    if (state !== "CONNECTED") return res.status(400).json({ error: `Sesión no conectada (estado: ${state})` });
    await session.sendText(`${telefono}@c.us`, mensaje);
    res.json({ status: "mensaje enviado" });
  } catch (err) {
    console.error("❌ Error enviando mensaje:", err);
    res.status(500).json({ error: "Error al enviar mensaje" });
  }
});

app.get("/estado-sesiones", async (req, res) => {
  const estados = [];
  try {
    const result = await pool.query("SELECT id, nombre, comercio FROM tenants");
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    
    for (const cliente of result.rows) {
      const clienteId = String(cliente.id);
      let estado = "NO_INICIADA";
      let tieneArchivos = false;
      
      // Verificar si tiene archivos de sesión en disco
      const sessionPath = path.join(sessionDir, clienteId);
      if (fs.existsSync(sessionPath)) {
        const defaultPath = path.join(sessionPath, "Default");
        tieneArchivos = fs.existsSync(defaultPath);
      }
      
      // También verificar en /app/tokens si no se encuentra en la ruta local
      if (!tieneArchivos && fs.existsSync(`/app/tokens/${clienteId}`)) {
        const defaultPath = path.join(`/app/tokens/${clienteId}`, "Default");
        tieneArchivos = fs.existsSync(defaultPath);
      }
      
      if (sessions[clienteId]) {
        try {
          estado = await sessions[clienteId].getConnectionState();
        } catch (err) {
          estado = "ERROR";
          console.error(`❌ Error obteniendo estado de ${clienteId}:`, err.message);
        }
      } else if (tieneArchivos) {
        estado = "ARCHIVOS_DISPONIBLES";
      }
      
      estados.push({ 
        clienteId, 
        nombre: cliente.nombre, 
        comercio: cliente.comercio, 
        estado,
        tieneArchivos,
        enMemoria: !!sessions[clienteId]
      });
    }
    res.json(estados);
  } catch (error) {
    console.error("❌ Error consultando clientes:", error);
    res.status(500).json({ error: "Error consultando clientes" });
  }
});

app.get("/restaurar/:clienteId", async (req, res) => {
  const { clienteId } = req.params;
  try {
    console.log(`🔄 Forzando restauración de sesión para cliente ${clienteId}...`);
    
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    let sessionPath = path.join(sessionDir, clienteId);
    
    // Si no existe en la ruta local, buscar en /app/tokens
    if (!fs.existsSync(sessionPath) && fs.existsSync(`/app/tokens/${clienteId}`)) {
      sessionPath = `/app/tokens/${clienteId}`;
      console.log(`📁 Usando ruta alternativa: ${sessionPath}`);
    }
    
    if (!fs.existsSync(sessionPath)) {
      return res.status(404).json({ 
        error: "No se encontraron archivos de sesión para este cliente",
        requiereQR: true 
      });
    }
    
    // Cerrar sesión actual si existe
    if (sessions[clienteId]) {
      try {
        await sessions[clienteId].close();
      } catch (e) {
        console.log("No se pudo cerrar la sesión anterior:", e.message);
      }
      delete sessions[clienteId];
    }
    
    // Configurar la ruta de sesión temporalmente
    const originalSessionFolder = process.env.SESSION_FOLDER;
    process.env.SESSION_FOLDER = path.dirname(sessionPath);
    
    try {
      // Restaurar desde archivos del disco
      await crearSesion(clienteId, false);
      
      // Verificar estado después de restaurar
      let estado = "UNKNOWN";
      if (sessions[clienteId]) {
        try {
          // Esperar un poco para que la sesión se establezca
          await new Promise(resolve => setTimeout(resolve, 3000));
          estado = await sessions[clienteId].getConnectionState();
        } catch (err) {
          estado = "ERROR";
        }
      }
      
      res.json({ 
        success: true, 
        mensaje: `Sesión restaurada para cliente ${clienteId}`,
        estado: estado,
        rutaUsada: sessionPath
      });
    } finally {
      // Restaurar la configuración original
      if (originalSessionFolder) {
        process.env.SESSION_FOLDER = originalSessionFolder;
      } else {
        delete process.env.SESSION_FOLDER;
      }
    }
  } catch (error) {
    console.error("❌ Error restaurando sesión:", error);
    res.status(500).json({ 
      error: "Error al restaurar sesión",
      details: error.message 
    });
  }
});

app.get("/debug/cliente/:clienteId", async (req, res) => {
  const { clienteId } = req.params;
  
  try {
    console.log(`🔍 Diagnóstico detallado para cliente ${clienteId}...`);
    
    const diagnostico = {
      clienteId,
      timestamp: new Date().toISOString(),
      baseDatos: {},
      carpetas: {},
      sesionMemoria: {},
      venom: {}
    };
    
    // 1. Verificar en base de datos
    try {
      const result = await pool.query("SELECT id, nombre, comercio FROM tenants WHERE id = $1", [clienteId]);
      diagnostico.baseDatos = {
        existe: result.rows.length > 0,
        datos: result.rows[0] || null
      };
    } catch (err) {
      diagnostico.baseDatos = { error: err.message };
    }
    
    // 2. Verificar carpetas en disco
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const rutasVerificar = [
      path.join(sessionDir, clienteId),
      `/app/tokens/${clienteId}`
    ];
    
    diagnostico.carpetas.rutas = {};
    for (const ruta of rutasVerificar) {
      const existe = fs.existsSync(ruta);
      diagnostico.carpetas.rutas[ruta] = {
        existe,
        archivos: existe ? fs.readdirSync(ruta) : [],
        defaultFolder: existe ? fs.existsSync(path.join(ruta, 'Default')) : false
      };
      
      if (existe) {
        const defaultPath = path.join(ruta, 'Default');
        if (fs.existsSync(defaultPath)) {
          diagnostico.carpetas.rutas[ruta].defaultFiles = fs.readdirSync(defaultPath);
          
          // Verificar archivos importantes de WhatsApp
          const archivosImportantes = [
            'Local Storage',
            'Session Storage', 
            'IndexedDB',
            'Preferences'
          ];
          
          diagnostico.carpetas.rutas[ruta].archivosImportantes = {};
          for (const archivo of archivosImportantes) {
            const archivoPath = path.join(defaultPath, archivo);
            diagnostico.carpetas.rutas[ruta].archivosImportantes[archivo] = fs.existsSync(archivoPath);
          }
        }
      }
    }
    
    // 3. Verificar sesión en memoria
    diagnostico.sesionMemoria = {
      existe: !!sessions[clienteId],
      tipo: sessions[clienteId] ? typeof sessions[clienteId] : null
    };
    
    if (sessions[clienteId]) {
      try {
        const estado = await sessions[clienteId].getConnectionState();
        diagnostico.sesionMemoria.estado = estado;
        
        const isConnected = await sessions[clienteId].isConnected();
        diagnostico.sesionMemoria.conectado = isConnected;
        
        // Intentar obtener información de la sesión
        try {
          const hostDevice = await sessions[clienteId].getHostDevice();
          diagnostico.sesionMemoria.dispositivo = hostDevice;
        } catch (e) {
          diagnostico.sesionMemoria.dispositivo = `Error: ${e.message}`;
        }
        
      } catch (err) {
        diagnostico.sesionMemoria.error = err.message;
      }
    }
    
    // 4. Verificar si Venom puede detectar la sesión
    try {
      // Simular las opciones que usaría venom
      const carpetaEncontrada = rutasVerificar.find(ruta => fs.existsSync(ruta));
      
      if (carpetaEncontrada) {
        diagnostico.venom = {
          carpetaDetectada: carpetaEncontrada,
          puedeRestaurar: fs.existsSync(path.join(carpetaEncontrada, 'Default')),
          configuracion: {
            session: clienteId,
            userDataDir: carpetaEncontrada,
            browserArgs: [
              '--no-sandbox',
              '--disable-setuid-sandbox', 
              '--disable-dev-shm-usage'
            ]
          }
        };
      } else {
        diagnostico.venom = {
          carpetaDetectada: null,
          puedeRestaurar: false,
          requiereQR: true
        };
      }
    } catch (err) {
      diagnostico.venom = { error: err.message };
    }
    
    console.log(`📋 Diagnóstico completo para cliente ${clienteId}:`, JSON.stringify(diagnostico, null, 2));
    
    res.json(diagnostico);
    
  } catch (error) {
    console.error(`❌ Error en diagnóstico de cliente ${clienteId}:`, error);
    res.status(500).json({ error: error.message });
  }
});

app.get("/debug/clientes", async (req, res) => {
  try {
    const result = await pool.query("SELECT id, nombre, comercio FROM tenants ORDER BY id");
    res.json({
      total: result.rows.length,
      clientes: result.rows
    });
  } catch (error) {
    console.error("❌ Error consultando clientes:", error);
    res.status(500).json({ error: "Error consultando clientes", details: error.message });
  }
});

app.get("/debug/carpetas", (req, res) => {
  const carpetas = [];
  
  if (fs.existsSync("/app/tokens")) {
    const folders = fs.readdirSync("/app/tokens").filter(item => {
      const itemPath = path.join("/app/tokens", item);
      return fs.statSync(itemPath).isDirectory() && !isNaN(item);
    });
    
    folders.forEach(folder => {
      const folderPath = path.join("/app/tokens", folder);
      const defaultPath = path.join(folderPath, "Default");
      carpetas.push({
        id: folder,
        ruta: folderPath,
        tieneDefault: fs.existsSync(defaultPath),
        archivos: fs.existsSync(folderPath) ? fs.readdirSync(folderPath) : []
      });
    });
  }
  
  res.json({
    rutaTokens: "/app/tokens",
    existeRuta: fs.existsSync("/app/tokens"),
    carpetas: carpetas
  });
});

app.post("/admin/limpiar-carpetas-huerfanas", async (req, res) => {
  try {
    // Obtener clientes activos de la DB
    const result = await pool.query("SELECT id FROM tenants");
    const clientesActivos = result.rows.map(row => String(row.id));
    
    if (!fs.existsSync("/app/tokens")) {
      return res.json({ mensaje: "No existe la carpeta /app/tokens", carpetasEliminadas: [] });
    }
    
    // Encontrar carpetas huérfanas
    const folders = fs.readdirSync("/app/tokens").filter(item => {
      const itemPath = path.join("/app/tokens", item);
      return fs.statSync(itemPath).isDirectory() && !isNaN(item);
    });
    
    const carpetasHuerfanas = folders.filter(folder => !clientesActivos.includes(folder));
    const carpetasEliminadas = [];
    
    for (const carpeta of carpetasHuerfanas) {
      const carpetaPath = path.join("/app/tokens", carpeta);
      try {
        // Eliminar recursivamente la carpeta
        fs.rmSync(carpetaPath, { recursive: true, force: true });
        carpetasEliminadas.push(carpeta);
        console.log(`🗑️ Carpeta eliminada: ${carpetaPath}`);
      } catch (err) {
        console.error(`❌ Error eliminando carpeta ${carpeta}:`, err.message);
      }
    }
    
    res.json({
      mensaje: `Limpieza completada. ${carpetasEliminadas.length} carpetas eliminadas.`,
      clientesActivos: clientesActivos,
      carpetasEncontradas: folders,
      carpetasHuerfanas: carpetasHuerfanas,
      carpetasEliminadas: carpetasEliminadas
    });
  } catch (error) {
    console.error("❌ Error limpiando carpetas huérfanas:", error);
    res.status(500).json({ error: "Error limpiando carpetas", details: error.message });
  }
});

app.delete("/limpiar-huerfanas", async (req, res) => {
  try {
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    if (!fs.existsSync(sessionDir)) {
      return res.json({ mensaje: "No existe carpeta de sesiones", eliminadas: [] });
    }
    
    // Obtener clientes válidos de la DB
    const result = await pool.query("SELECT id FROM tenants");
    const clientesValidos = result.rows.map(r => String(r.id));
    
    // Encontrar carpetas huérfanas
    const carpetas = fs.readdirSync(sessionDir).filter(item => {
      const itemPath = path.join(sessionDir, item);
      return fs.statSync(itemPath).isDirectory() && !isNaN(item);
    });
    
    const carpetasHuerfanas = carpetas.filter(c => !clientesValidos.includes(c));
    const eliminadas = [];
    
    for (const carpeta of carpetasHuerfanas) {
      try {
        const carpetaPath = path.join(sessionDir, carpeta);
        console.log(`🗑️ Eliminando carpeta huérfana: ${carpetaPath}`);
        
        // Cerrar sesión si está activa
        if (sessions[carpeta]) {
          await sessions[carpeta].close();
          delete sessions[carpeta];
        }
        
        // Eliminar carpeta recursivamente
        fs.rmSync(carpetaPath, { recursive: true, force: true });
        eliminadas.push(carpeta);
        console.log(`✅ Carpeta ${carpeta} eliminada`);
      } catch (err) {
        console.error(`❌ Error eliminando carpeta ${carpeta}:`, err);
      }
    }
    
    res.json({
      mensaje: `Limpieza completada. ${eliminadas.length} carpetas eliminadas.`,
      carpetas_eliminadas: eliminadas,
      clientes_validos: clientesValidos
    });
  } catch (err) {
    console.error("❌ Error limpiando carpetas huérfanas:", err);
    res.status(500).json({ error: "Error limpiando carpetas" });
  }
});

app.post("/forzar-nueva-sesion/:clienteId", async (req, res) => {
  const { clienteId } = req.params;
  
  try {
    console.log(`🔄 Forzando nueva sesión para cliente ${clienteId}...`);
    
    // 1. Verificar que el cliente existe en la base de datos
    try {
      const result = await pool.query("SELECT id, comercio FROM tenants WHERE id = $1", [clienteId]);
      if (result.rows.length === 0) {
        return res.status(404).json({ 
          error: `Cliente ${clienteId} no existe en la base de datos`,
          accion: "verificar_cliente"
        });
      }
      console.log(`✅ Cliente ${clienteId} encontrado en BD: ${result.rows[0].comercio}`);
    } catch (dbError) {
      console.error(`❌ Error verificando cliente en BD: ${dbError.message}`);
      // Continuar anyway si hay problemas de BD
    }
    
    // 2. Cerrar sesión existente si está en memoria
    if (sessions[clienteId]) {
      console.log(`🔒 Cerrando sesión existente en memoria para ${clienteId}...`);
      try {
        await sessions[clienteId].close();
        console.log(`✅ Sesión en memoria cerrada para ${clienteId}`);
      } catch (closeError) {
        console.log(`⚠️ Error cerrando sesión: ${closeError.message}`);
      }
      delete sessions[clienteId];
    }
    
    // 3. Limpiar archivos de sesión existentes si los hay
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const rutasLimpiar = [
      path.join(sessionDir, clienteId),
      `/app/tokens/${clienteId}`
    ];
    
    for (const rutaLimpiar of rutasLimpiar) {
      if (fs.existsSync(rutaLimpiar)) {
        console.log(`🗑️ Eliminando carpeta de sesión: ${rutaLimpiar}`);
        try {
          fs.rmSync(rutaLimpiar, { recursive: true, force: true });
          console.log(`✅ Carpeta eliminada: ${rutaLimpiar}`);
        } catch (deleteError) {
          console.log(`⚠️ Error eliminando carpeta: ${deleteError.message}`);
        }
      }
    }
    
    // 4. Limpiar QR en base de datos
    try {
      await pool.query("UPDATE tenants SET qr_code = NULL WHERE id = $1", [clienteId]);
      console.log(`✅ QR limpiado en base de datos para cliente ${clienteId}`);
    } catch (dbError) {
      console.log(`⚠️ Error limpiando QR en BD: ${dbError.message}`);
    }
    
    // 5. Crear nueva sesión desde cero
    console.log(`🚀 Creando nueva sesión desde cero para cliente ${clienteId}...`);
    
    try {
      await crearSesionConTimeout(clienteId, 45000, true); // 45 segundos timeout, generar QR
      
      // Verificar que el QR se haya generado
      const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
      const qrPath = path.join(sessionDir, `${clienteId}.html`);
      
      let qrGenerado = false;
      let qrEnDB = false;
      
      // Verificar archivo QR
      if (fs.existsSync(qrPath)) {
        qrGenerado = true;
        console.log(`✅ Archivo QR generado para cliente ${clienteId}`);
      }
      
      // Verificar QR en base de datos
      try {
        const qrCheck = await pool.query("SELECT qr_code FROM tenants WHERE id = $1", [clienteId]);
        if (qrCheck.rows.length > 0 && qrCheck.rows[0].qr_code) {
          qrEnDB = true;
          console.log(`✅ QR guardado en base de datos para cliente ${clienteId}`);
        }
      } catch (dbError) {
        console.log(`⚠️ Error verificando QR en DB: ${dbError.message}`);
      }
      
      const response = {
        success: true,
        mensaje: `Nueva sesión creada para cliente ${clienteId}`,
        accion: "escanear_qr",
        qr_url: `/qr/${clienteId}`,
        timestamp: new Date().toISOString(),
        verificacion: {
          qr_archivo_generado: qrGenerado,
          qr_guardado_en_db: qrEnDB,
          ruta_qr: qrPath
        }
      };
      
      console.log(`✅ Nueva sesión creada exitosamente para cliente ${clienteId}`);
      res.json(response);
      
    } catch (createError) {
      console.error(`❌ Error creando nueva sesión: ${createError.message}`);
      res.status(500).json({
        error: "Error creando nueva sesión",
        details: createError.message,
        accion: "reintentar",
        qr_url: `/qr/${clienteId}` // Intentar mostrar QR anyway
      });
    }
    
  } catch (error) {
    console.error(`❌ Error en forzar-nueva-sesion para ${clienteId}:`, error);
    res.status(500).json({ 
      error: "Error interno del servidor",
      details: error.message 
    });
  }
});

app.post("/notificar-chat-humano", async (req, res) => {
  try {
    const { cliente_id, telefono, mensaje, tipo } = req.body;
    
    if (!cliente_id || !telefono) {
      return res.status(400).json({ error: "cliente_id y telefono son requeridos" });
    }
    
    console.log(`🚨 ==========================================`);
    console.log(`🚨 ALERTA: ATENCIÓN HUMANA REQUERIDA`);
    console.log(`🚨 ==========================================`);
    console.log(`📞 Cliente ID: ${cliente_id}`);
    console.log(`📱 Teléfono: ${telefono}`);
    console.log(`💬 Último mensaje: ${mensaje}`);
    console.log(`🔔 Tipo: ${tipo || 'solicitud_ayuda'}`);
    console.log(`⏰ Fecha: ${new Date().toLocaleString('es-AR')}`);
    
    // Buscar información del cliente en la base de datos
    let comercio = 'N/A';
    let nombre = 'N/A';
    try {
      const clienteInfo = await pool.query("SELECT comercio, nombre FROM tenants WHERE id = $1", [cliente_id]);
      if (clienteInfo.rows.length > 0) {
        comercio = clienteInfo.rows[0].comercio || 'N/A';
        nombre = clienteInfo.rows[0].nombre || 'N/A';
        console.log(`🏢 Comercio: ${comercio}`);
        console.log(`👤 Contacto: ${nombre}`);
      }
    } catch (err) {
      console.log(`⚠️ No se pudo obtener info del cliente: ${err.message}`);
    }
    
    console.log(`🚨 ==========================================`);
    console.log(`💡 El usuario puede escribir "Bot" para volver al asistente virtual`);
    console.log(`🚨 ==========================================`);
    
    // Nota: Autonotificación removida como se solicitó
    console.log(`ℹ️ Notificación registrada. El administrador debe monitorear manualmente las solicitudes de ayuda.`);
    
    res.json({ 
      success: true, 
      mensaje: "Notificación de chat humano registrada",
      cliente_id,
      telefono,
      action: "logged_only",
      nota: "Autonotificación deshabilitada"
    });
  } catch (error) {
    console.error("❌ Error procesando notificación de chat humano:", error);
    res.status(500).json({ error: "Error procesando notificación", details: error.message });
  }
});

app.listen(PORT, async () => {
  console.log(`✅ Venom-service corriendo en puerto ${PORT}`);
  console.log(`📁 Carpeta de sesiones configurada: ${process.env.SESSION_FOLDER || path.join(__dirname, "tokens")}`);
  console.log(`🔍 Verificando si existe /app/tokens:`, fs.existsSync("/app/tokens"));
  
  if (fs.existsSync("/app/tokens")) {
    const folders = fs.readdirSync("/app/tokens").filter(item => !isNaN(item));
    console.log(`📂 Carpetas numéricas encontradas en /app/tokens:`, folders);
  }
  
  // Esperar un poco para asegurar que la DB esté lista
  console.log("⏱️ Esperando conexión estable a la base de datos...");
  await new Promise(resolve => setTimeout(resolve, 3000));
  
  await restaurarSesiones();
});

app.post("/crear-sesiones-faltantes", async (req, res) => {
  try {
    // Obtener clientes de la DB
    const result = await pool.query("SELECT id, comercio FROM tenants");
    const clientesActivos = result.rows.map(row => String(row.id));
    
    // Obtener carpetas existentes
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const carpetas = fs.existsSync(sessionDir) ? 
      fs.readdirSync(sessionDir).filter(item => {
        const itemPath = path.join(sessionDir, item);
        return fs.statSync(itemPath).isDirectory() && !isNaN(item);
      }) : [];
    
    const clientesSinCarpetas = clientesActivos.filter(id => !carpetas.includes(id));
    
    if (clientesSinCarpetas.length === 0) {
      return res.json({
        mensaje: "Todos los clientes ya tienen sesiones creadas",
        clientes_activos: clientesActivos,
        carpetas_existentes: carpetas
      });
    }
    
    console.log(`🚀 API: Creando ${clientesSinCarpetas.length} sesiones faltantes...`);
    const resultados = [];
    
    for (const clienteId of clientesSinCarpetas) {
      try {
        console.log(`⚙️ API: Creando sesión para cliente ${clienteId}...`);
        
        await Promise.race([
          crearSesion(clienteId, true),
          new Promise((_, reject) => 
            setTimeout(() => reject(new Error("Timeout de 25 segundos")), 25000)
          )
        ]);
        
        resultados.push({
          cliente_id: clienteId,
          estado: "creado",
          qr_url: `/qr/${clienteId}`
        });
        
        console.log(`✅ API: Sesión creada para cliente ${clienteId}`);
        
        // Esperar entre creaciones
        await new Promise(resolve => setTimeout(resolve, 2000));
        
      } catch (err) {
        console.error(`❌ API: Error con cliente ${clienteId}:`, err.message);
        resultados.push({
          cliente_id: clienteId,
          estado: "error",
          error: err.message
        });
      }
    }
    
    res.json({
      mensaje: `Proceso completado. ${resultados.filter(r => r.estado === 'creado').length} sesiones creadas.`,
      resultados: resultados,
      clientes_procesados: clientesSinCarpetas.length
    });
    
  } catch (error) {
    console.error("❌ Error en crear-sesiones-faltantes:", error);
    res.status(500).json({ error: "Error creando sesiones", details: error.message });
  }
});

// Endpoint de salud para verificar estado de sesiones
app.get("/health", async (req, res) => {
  try {
    const estadoSesiones = {};
    
    for (const clienteId in sessions) {
      try {
        const isConnected = await verificarEstadoSesion(clienteId);
        const state = sessions[clienteId] ? await sessions[clienteId].getConnectionState() : 'NO_SESSION';
        
        estadoSesiones[clienteId] = {
          conectada: isConnected,
          estado: state,
          existe: !!sessions[clienteId]
        };
      } catch (e) {
        estadoSesiones[clienteId] = {
          conectada: false,
          estado: 'ERROR',
          existe: !!sessions[clienteId],
          error: e.message
        };
      }
    }
    
    const totalSesiones = Object.keys(sessions).length;
    const sesionesConectadas = Object.values(estadoSesiones).filter(s => s.conectada).length;
    
    res.json({
      status: "healthy",
      timestamp: new Date().toISOString(),
      sesiones: {
        total: totalSesiones,
        conectadas: sesionesConectadas,
        desconectadas: totalSesiones - sesionesConectadas,
        detalle: estadoSesiones
      }
    });
  } catch (error) {
    res.status(500).json({
      status: "unhealthy",
      error: error.message,
      timestamp: new Date().toISOString()
    });
  }
});

// Endpoint para reconectar una sesión específica
app.post("/reconectar/:clienteId", async (req, res) => {
  const clienteId = req.params.clienteId;
  
  try {
    console.log(`🔄 Solicitud manual de reconexión para sesión ${clienteId}`);
    
    // Verificar que el cliente existe en la DB
    const cliente = await pool.query("SELECT id, comercio FROM tenants WHERE id = $1", [clienteId]);
    if (cliente.rows.length === 0) {
      return res.status(404).json({ error: "Cliente no encontrado en la base de datos" });
    }
    
    await reconectarSesion(clienteId);
    
    res.json({
      success: true,
      message: `Reconexión iniciada para sesión ${clienteId}`,
      cliente: cliente.rows[0].comercio
    });
  } catch (error) {
    console.error(`❌ Error en reconexión manual ${clienteId}:`, error);
    res.status(500).json({
      error: "Error iniciando reconexión",
      details: error.message
    });
  }
});

// Endpoint para reconectar todas las sesiones desconectadas
app.post("/reconectar-todas", async (req, res) => {
  try {
    console.log(`🔄 Solicitud manual de reconexión para todas las sesiones`);
    
    const sesionesDesconectadas = [];
    const errores = [];
    
    for (const clienteId in sessions) {
      const estaConectada = await verificarEstadoSesion(clienteId);
      
      if (!estaConectada) {
        sesionesDesconectadas.push(clienteId);
        
        try {
          await reconectarSesion(clienteId);
        } catch (error) {
          errores.push({ clienteId, error: error.message });
        }
      }
    }
    
    res.json({
      success: true,
      message: "Reconexión masiva iniciada",
      sesionesDesconectadas,
      errores,
      total: sesionesDesconectadas.length
    });
  } catch (error) {
    console.error("❌ Error en reconexión masiva:", error);
    res.status(500).json({
      error: "Error iniciando reconexión masiva",
      details: error.message
    });
  }
});

app.post("/generar-qr/:clienteId", async (req, res) => {
  const { clienteId } = req.params;
  
  try {
    console.log(`🎯 Solicitud específica de generación QR para cliente ${clienteId}...`);
    
    // 1. Verificar que el cliente existe en la base de datos
    try {
      const result = await pool.query("SELECT id, comercio FROM tenants WHERE id = $1", [clienteId]);
      if (result.rows.length === 0) {
        return res.status(404).json({ 
          error: `Cliente ${clienteId} no existe en la base de datos`,
          success: false
        });
      }
      console.log(`✅ Cliente ${clienteId} encontrado: ${result.rows[0].comercio}`);
    } catch (dbError) {
      console.error(`❌ Error verificando cliente en BD: ${dbError.message}`);
      return res.status(500).json({ error: "Error verificando cliente en base de datos" });
    }
    
    // 2. Resetear contador de errores para permitir nueva generación
    sessionErrors[clienteId] = 0;
    
    // 3. Cerrar sesión existente si está en memoria
    if (sessions[clienteId]) {
      console.log(`🔒 Cerrando sesión existente para regenerar QR ${clienteId}...`);
      try {
        await sessions[clienteId].close();
        console.log(`✅ Sesión cerrada para ${clienteId}`);
      } catch (closeError) {
        console.log(`⚠️ Error cerrando sesión: ${closeError.message}`);
      }
      delete sessions[clienteId];
    }
    
    // 4. Limpiar archivos de sesión existentes
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const rutasLimpiar = [
      path.join(sessionDir, clienteId),
      `/app/tokens/${clienteId}`
    ];
    
    for (const rutaLimpiar of rutasLimpiar) {
      if (fs.existsSync(rutaLimpiar)) {
        console.log(`🗑️ Eliminando carpeta de sesión: ${rutaLimpiar}`);
        try {
          fs.rmSync(rutaLimpiar, { recursive: true, force: true });
          console.log(`✅ Carpeta eliminada: ${rutaLimpiar}`);
        } catch (deleteError) {
          console.log(`⚠️ Error eliminando carpeta: ${deleteError.message}`);
        }
      }
    }
    
    // 5. Generar nuevo QR
    console.log(`🚀 Generando nuevo QR para cliente ${clienteId}...`);
    
    await crearSesionConTimeout(clienteId, 30000, true); // Solo 30 segundos, generar QR
    
    // 6. Verificar que el QR se haya generado
    const qrPath = path.join(sessionDir, `${clienteId}.html`);
    
    let qrGenerado = false;
    let qrEnDB = false;
    
    // Verificar archivo QR
    if (fs.existsSync(qrPath)) {
      qrGenerado = true;
      console.log(`✅ Archivo QR generado para cliente ${clienteId}`);
    }
    
    // Verificar QR en base de datos
    try {
      const qrCheck = await pool.query("SELECT qr_code FROM tenants WHERE id = $1", [clienteId]);
      if (qrCheck.rows.length > 0 && qrCheck.rows[0].qr_code) {
        qrEnDB = true;
        console.log(`✅ QR guardado en base de datos para cliente ${clienteId}`);
      }
    } catch (dbError) {
      console.log(`⚠️ Error verificando QR en DB: ${dbError.message}`);
    }
    
    res.json({
      success: true,
      mensaje: `QR generado exitosamente para cliente ${clienteId}`,
      cliente_id: clienteId,
      qr_url: `/qr/${clienteId}`,
      timestamp: new Date().toISOString(),
      verificacion: {
        qr_archivo_generado: qrGenerado,
        qr_guardado_en_db: qrEnDB,
        ruta_qr: qrPath
      }
    });
    
  } catch (error) {
    console.error(`❌ Error generando QR para ${clienteId}:`, error);
    res.status(500).json({
      success: false,
      error: "Error generando QR",
      details: error.message,
      cliente_id: clienteId
    });
  }
});

app.post("/reset-errores/:clienteId", async (req, res) => {
  const { clienteId } = req.params;
  
  try {
    const erroresAnteriores = sessionErrors[clienteId] || 0;
    sessionErrors[clienteId] = 0;
    
    console.log(`🔄 Contador de errores reseteado para cliente ${clienteId} (era: ${erroresAnteriores})`);
    
    res.json({
      success: true,
      mensaje: `Contador de errores reseteado para cliente ${clienteId}`,
      errores_anteriores: erroresAnteriores,
      cliente_id: clienteId
    });
  } catch (error) {
    console.error(`❌ Error reseteando errores para ${clienteId}:`, error);
    res.status(500).json({
      success: false,
      error: "Error reseteando contador de errores",
      details: error.message
    });
  }
});

app.post("/limpiar-locks/:clienteId", async (req, res) => {
  const { clienteId } = req.params;
  
  try {
    console.log(`🧹 Limpiando archivos de bloqueo para cliente ${clienteId}...`);
    
    const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const sessionPath = path.join(sessionDir, clienteId);
    const singletonLockPath = path.join(sessionPath, "SingletonLock");
    
    let archivosLimpiados = [];
    
    // Limpiar SingletonLock
    if (fs.existsSync(singletonLockPath)) {
      try {
        fs.unlinkSync(singletonLockPath);
        archivosLimpiados.push("SingletonLock");
        console.log(`🔓 Archivo SingletonLock eliminado para cliente ${clienteId}`);
      } catch (e) {
        console.log(`⚠️ Error eliminando SingletonLock: ${e.message}`);
      }
    }
    
    // También buscar en /app/tokens si es diferente
    if (fs.existsSync(`/app/tokens/${clienteId}`)) {
      const altSingletonPath = path.join(`/app/tokens/${clienteId}`, "SingletonLock");
      if (fs.existsSync(altSingletonPath)) {
        try {
          fs.unlinkSync(altSingletonPath);
          archivosLimpiados.push("SingletonLock (alternativo)");
          console.log(`🔓 Archivo SingletonLock alternativo eliminado para cliente ${clienteId}`);
        } catch (e) {
          console.log(`⚠️ Error eliminando SingletonLock alternativo: ${e.message}`);
        }
      }
    }
    
    res.json({
      success: true,
      mensaje: `Archivos de bloqueo limpiados para cliente ${clienteId}`,
      archivos_limpiados: archivosLimpiados,
      cliente_id: clienteId
    });
    
  } catch (error) {
    console.error(`❌ Error limpiando locks para ${clienteId}:`, error);
    res.status(500).json({
      success: false,
      error: "Error limpiando archivos de bloqueo",
      details: error.message
    });
  }
});

app.get("/debug/errores", (req, res) => {
  res.json({
    session_errors: sessionErrors,
    total_clientes_con_errores: Object.keys(sessionErrors).length,
    clientes_bloqueados: Object.entries(sessionErrors)
      .filter(([clienteId, errores]) => errores >= 5)
      .map(([clienteId, errores]) => ({ clienteId, errores }))
  });
});
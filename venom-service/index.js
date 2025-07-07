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
    const timer = setTimeout(() => reject(new Error("⏱ Tiempo de espera agotado para crear sesión")), timeoutMs);
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

  // Si se pide regenerar QR, borra el archivo, la sesión en memoria y el campo en la base
  if (permitirGuardarQR) {
    if (fs.existsSync(qrPath)) fs.unlinkSync(qrPath);
    if (sessions[sessionId]) {
      try {
        await sessions[sessionId].close();
      } catch (e) {
        console.log("No se pudo cerrar la sesión anterior:", e);
      }
      delete sessions[sessionId];
    }
    try {
      await pool.query("UPDATE tenants SET qr_code = NULL WHERE id = $1", [sessionId]);
    } catch (err) {
      console.error("❌ Error limpiando QR en DB:", err);
    }
  }

  if (sessions[sessionId]) {
    console.log(`🟡 Sesión ya activa para ${sessionId}`);
    return sessions[sessionId];
  }

  console.log(`⚙️ Iniciando sesión para ${sessionId}...`);
  if (!fs.existsSync(sessionDir)) {
    fs.mkdirSync(sessionDir);
    console.log("📁 Carpeta 'sessions' creada");
  }

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
        if (!permitirGuardarQR) return;
        const html = `<html><body style="display:flex;justify-content:center;align-items:center;height:100vh;"><img src="${base64Qr}" /></body></html>`;
        const qrPath = path.join(sessionDir, `${sessionId}.html`);
        fs.writeFileSync(qrPath, html);

        try {
          const result = await pool.query(
            "UPDATE tenants SET qr_code = $1 WHERE id = $2",
            [base64Qr.replace(/^data:image\/\w+;base64,/, ""), sessionId]
          );
          console.log(`📬 QR guardado en DB para cliente ${sessionId}`, result.rowCount);
        } catch (err) {
          console.error("❌ Error guardando QR en DB:", err);
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
        if (reconexionIntentos < maxIntentos) {
          reconexionIntentos++;
          console.log(`🔄 Intento ${reconexionIntentos}/${maxIntentos} de reconexión para ${sessionId}...`);
          
          // Esperar antes de intentar reconectar
          await new Promise(resolve => setTimeout(resolve, 5000));
          
          try {
            // Cerrar sesión actual antes de recrear
            if (sessions[sessionId]) {
              await sessions[sessionId].close();
              delete sessions[sessionId];
            }
            
            await crearSesion(sessionId, false);
            console.log(`✅ Sesión ${sessionId} reconectada en intento ${reconexionIntentos}`);
          } catch (err) {
            console.error(`❌ Error en intento ${reconexionIntentos} de reconexión ${sessionId}:`, err.message);
            
            if (reconexionIntentos >= maxIntentos) {
              console.error(`🚫 Máximo de intentos alcanzado para ${sessionId}, requiere intervención manual`);
            }
          }
        } else {
          console.error(`🚫 Sesión ${sessionId} desconectada permanentemente, requiere escaneo de QR`);
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

    return client;
  } catch (err) {
    console.error(`❌ Error creando sesión para ${sessionId}:`, err);
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
    
    // Buscar todos los clientes que tienen carpetas de sesión en el disco
    if (!fs.existsSync(sessionDir)) {
      console.log("📁 No existe carpeta de sesiones, creándola...");
      fs.mkdirSync(sessionDir, { recursive: true });
      return;
    }

    // Leer todas las carpetas de sesión del disco
    const sessionFolders = fs.readdirSync(sessionDir).filter(item => {
      const itemPath = path.join(sessionDir, item);
      return fs.statSync(itemPath).isDirectory() && !isNaN(item);
    });

    console.log(`📂 Encontradas ${sessionFolders.length} carpetas de sesión en disco`);

    // Si no hay carpetas pero existe la carpeta /app/tokens, buscar ahí
    if (sessionFolders.length === 0 && fs.existsSync("/app/tokens")) {
      console.log("🔍 Buscando en /app/tokens...");
      const appTokenFolders = fs.readdirSync("/app/tokens").filter(item => {
        const itemPath = path.join("/app/tokens", item);
        return fs.statSync(itemPath).isDirectory() && !isNaN(item);
      });
      console.log(`📂 Encontradas ${appTokenFolders.length} carpetas en /app/tokens`);
      
      // Copiar carpetas encontradas al array principal
      sessionFolders.push(...appTokenFolders.map(folder => {
        // Si las carpetas están en /app/tokens, usar esa ruta
        return { id: folder, path: path.join("/app/tokens", folder) };
      }));
    } else {
      // Agregar path completo a las carpetas encontradas
      sessionFolders.forEach((folder, index) => {
        sessionFolders[index] = { id: folder, path: path.join(sessionDir, folder) };
      });
    }

    // Verificar cuáles clientes existen en la base de datos
    let result;
    try {
      result = await pool.query("SELECT id, comercio FROM tenants");
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
    
    // Verificar clientes activos que NO tienen carpetas de sesión
    console.log("🔍 Verificando clientes activos sin carpetas de sesión...");
    const carpetasExistentes = sessionFolders.map(sf => typeof sf === 'string' ? sf : sf.id);
    const clientesSinCarpetas = clientesActivos.filter(id => !carpetasExistentes.includes(id));
    
    if (clientesSinCarpetas.length > 0) {
      console.log(`📋 Clientes sin carpetas de sesión:`, clientesSinCarpetas);
      console.log(`💡 Para crear sesiones nuevas, usa: /iniciar/{clienteId}`);
      console.log(`📱 Ejemplos:`);
      clientesSinCarpetas.forEach(id => {
        console.log(`  - /iniciar/${id} (luego escanear QR en /qr/${id})`);
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
    await crearSesionConTimeout(clienteId, 60000, true); // <-- true para guardar QR
    res.send(`✅ Sesión iniciada para ${clienteId}. Escaneá el QR en /qr/${clienteId}`);
  } catch (error) {
    console.error("❌ Error al iniciar sesión:", error);
    res.status(500).send("Error al iniciar sesión");
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
    console.log(`� Teléfono: ${telefono}`);
    console.log(`💬 Último mensaje: ${mensaje}`);
    console.log(`🔔 Tipo: ${tipo || 'solicitud_ayuda'}`);
    console.log(`⏰ Fecha: ${new Date().toLocaleString('es-AR')}`);
    console.log(`🚨 ==========================================`);
    
    // Buscar información del cliente en la base de datos
    try {
      const clienteInfo = await pool.query("SELECT comercio, nombre FROM tenants WHERE id = $1", [cliente_id]);
      if (clienteInfo.rows.length > 0) {
        const { comercio, nombre } = clienteInfo.rows[0];
        console.log(`🏢 Comercio: ${comercio || 'N/A'}`);
        console.log(`👤 Contacto: ${nombre || 'N/A'}`);
      }
    } catch (err) {
      console.log(`⚠️ No se pudo obtener info del cliente: ${err.message}`);
    }
    
    // Intentar enviar un mensaje de confirmación al teléfono para que aparezca como "sin leer"
    const session = sessions[String(cliente_id)];
    if (session) {
      try {
        const estado = await session.getConnectionState();
        if (estado === "CONNECTED") {
          // Enviar un mensaje de sistema que quede como "sin leer" para el operador
          await session.sendText(`${telefono}@c.us`, "🔔 *Notificación del sistema*: Se ha registrado tu solicitud de ayuda. Un asesor revisará este chat pronto.");
          console.log(`✅ Mensaje de notificación enviado a ${telefono}`);
        } else {
          console.log(`⚠️ Sesión ${cliente_id} no conectada (${estado}), no se pudo enviar notificación por WhatsApp`);
        }
      } catch (err) {
        console.log(`❌ Error enviando mensaje de notificación: ${err.message}`);
      }
    } else {
      console.log(`⚠️ No hay sesión activa para cliente ${cliente_id}`);
    }
    
    console.log(`🚨 ==========================================`);
    
    res.json({ 
      success: true, 
      mensaje: "Notificación de chat humano registrada y procesada",
      cliente_id,
      telefono,
      notificacion_enviada: !!session
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
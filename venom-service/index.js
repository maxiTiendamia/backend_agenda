require("dotenv").config();
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

// **NUEVA FUNCIÓN: Verificar conectividad del backend**
async function verificarConectividadBackend() {
  try {
    console.log("🔍 Verificando conectividad del backend...");
    const response = await axios.get(
      "https://backend-agenda-2.onrender.com/api/webhook",
      {
        timeout: 10000,
        validateStatus: function (status) {
          return status < 500; // Considerar OK cualquier status menor a 500
        },
      }
    );
    console.log(`✅ Backend accesible - Status: ${response.status}`);
    return true;
  } catch (err) {
    console.error(
      "❌ Error verificando conectividad del backend:",
      err.message
    );
    if (err.code === "ECONNRESET" || err.code === "ECONNREFUSED") {
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
        cliente_id: clienteId,
      },
      { timeout: 10000 }
    );

    console.log(
      `✅ Webhook test exitoso - Status: ${testResponse.status}`,
      testResponse.data
    );
    return true;
  } catch (err) {
    console.error(
      `❌ Error en test del webhook para cliente ${clienteId}:`,
      err.message
    );
    if (err.response) {
      console.error("❌ Respuesta del webhook con error:", {
        status: err.response.status,
        data: err.response.data,
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
    console.log(
      `🚫 Cliente ${clienteId} bloqueado por errores (${sessionErrors[clienteId]}), cancelando reconexión automática`
    );
    return; // No programar más reintentos
  }

  // Limpiar sesión anterior si existe
  if (sessions[clienteId]) {
    try {
      if (
        sessions[clienteId].client &&
        typeof sessions[clienteId].client.close === "function"
      ) {
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

    // Solo programar reintento si NO está bloqueado por errores (límite más estricto)
    if (!sessionErrors[clienteId] || sessionErrors[clienteId] < 3) {
      console.log(
        `⏳ Programando reintento de reconexión para ${clienteId} en 60 segundos...`
      );
      setTimeout(() => {
        reconectarSesion(clienteId);
      }, 60000); // Reintentar en 60 segundos (más tiempo)
    } else {
      console.log(
        `🚫 No se programará más reintentos para ${clienteId} (bloqueado por errores)`
      );
    }
  }
}

// Función para monitorear todas las sesiones
async function monitorearSesiones() {
  for (const clienteId in sessions) {
    const estaConectada = await verificarEstadoSesion(clienteId);

    if (!estaConectada) {
      console.log(
        `🔍 Sesión ${clienteId} desconectada, iniciando reconexión...`
      );

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

pool
  .connect()
  .then(async (client) => {
    console.log("✅ Conexión a PostgreSQL exitosa");
    client.release();

    // Verificar qué clientes existen en la base de datos
    try {
      const result = await pool.query("SELECT id, comercio FROM tenants");
      console.log(
        `📊 Clientes encontrados en DB:`,
        result.rows.map((r) => `${r.id}(${r.comercio})`)
      );
    } catch (err) {
      console.error("❌ Error verificando clientes en DB:", err);
    }
  })
  .catch((err) => {
    console.error("❌ Error al conectar con la base de datos:", err);
  });

function crearSesionConTimeout(
  clienteId,
  timeoutMs = 60000,
  permitirGuardarQR = true
) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      console.log(
        `⏱️ Timeout alcanzado para sesión ${clienteId} (${timeoutMs}ms)`
      );
      reject(new Error("⏱ Tiempo de espera agotado para crear sesión"));
    }, timeoutMs);

    crearSesion(clienteId, permitirGuardarQR)
      .then((res) => {
        clearTimeout(timer);
        resolve(res);
      })
      .catch((err) => {
        clearTimeout(timer);
        reject(err);
      });
  });
}

async function crearSesion(clienteId, permitirGuardarQR = true) {
  const sessionId = String(clienteId);
  const sessionDir =
    process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
  const qrPath = path.join(sessionDir, `${sessionId}.html`);

  console.log(
    `⚙️ Iniciando crearSesion para cliente ${sessionId}, permitirGuardarQR: ${permitirGuardarQR}`
  );

  // **NUEVO: Limpiar SingletonLock antes de crear la sesión**
  const searchPaths = [sessionDir, "/app/tokens"];
  for (const searchPath of searchPaths) {
    const singletonPath = path.join(searchPath, sessionId, "SingletonLock");
    if (fs.existsSync(singletonPath)) {
      try {
        fs.unlinkSync(singletonPath);
        console.log(
          `🔓 Limpiado SingletonLock previo para cliente ${sessionId} en ${searchPath}`
        );
      } catch (err) {
        console.error(
          `❌ Error limpiando SingletonLock en ${searchPath}:`,
          err.message
        );
      }
    }
  }

  // Verificar si esta sesión está en bucle de errores (reducido a 3 intentos)
  if (sessionErrors[sessionId] && sessionErrors[sessionId] >= 3) {
    console.log(
      `🚫 Cliente ${sessionId} tiene demasiados errores consecutivos (${sessionErrors[sessionId]}), bloqueado por 30 minutos`
    );

    // Bloquear por 30 minutos
    setTimeout(() => {
      console.log(
        `🔓 Desbloqueando cliente ${sessionId} después de 30 minutos`
      );
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
        console.log(
          `⚠️ Error cerrando sesión anterior para ${sessionId}:`,
          e.message
        );
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
          console.error(
            `❌ Error limpiando carpeta ${pathToClean}:`,
            err.message
          );
        }
      }
    }

    // Crear carpeta nueva limpia
    await crearCarpetasAutomaticamente();

    try {
      const result = await pool.query(
        "UPDATE tenants SET qr_code = NULL WHERE id = $1",
        [sessionId]
      );
      console.log(
        `🧹 QR limpiado en DB para cliente ${sessionId}, filas afectadas: ${result.rowCount}`
      );
    } catch (err) {
      console.error(
        `❌ Error limpiando QR en DB para cliente ${sessionId}:`,
        err
      );
    }
  }

  if (sessions[sessionId]) {
    console.log(`🟡 Sesión ya activa para ${sessionId}`);
    return sessions[sessionId];
  }

  console.log(`⚙️ Iniciando nueva sesión venom para ${sessionId}...`);
  console.log(`📁 Directorio de sesiones: ${sessionDir}`);
  console.log(
    `🎯 Ruta específica de esta sesión: ${path.join(sessionDir, sessionId)}`
  );

  if (!fs.existsSync(sessionDir)) {
    fs.mkdirSync(sessionDir, { recursive: true });
    console.log("📁 Carpeta 'sessions' creada");
  }

  // **CRÍTICO: Verificar que no existan otras sesiones interferentes**
  const carpetasExistentes = fs.readdirSync(sessionDir).filter((item) => {
    const fullPath = path.join(sessionDir, item);
    return fs.statSync(fullPath).isDirectory() && item !== sessionId;
  });

  if (carpetasExistentes.length > 0) {
    console.log(
      `⚠️ ADVERTENCIA: Existen otras carpetas de sesión que podrían interferir:`
    );
    carpetasExistentes.forEach((carpeta) => {
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
      ],
      puppeteerOptions: {
        headless: "new",
        args: ["--no-sandbox", "--disable-setuid-sandbox"],
      },
      createPathFileToken: true,
      catchQR: async (base64Qr) => {
        // Solo procesar QR si se solicita explícitamente y no se ha guardado ya para esta sesión específica
        if (!permitirGuardarQR) {
          console.log(
            `ℹ️ QR generado para cliente ${sessionId} pero no se guardará (permitirGuardarQR=false)`
          );
          return;
        }

        if (qrGuardado) {
          console.log(
            `⚠️ QR ya fue procesado para esta sesión específica ${sessionId}, saltando...`
          );
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
            console.log(
              `📬 QR guardado exitosamente en DB para cliente ${sessionId}`
            );
            qrGuardado = true; // Solo marcar como guardado si todo fue exitoso
          } else {
            console.error(
              `❌ No se pudo actualizar QR en DB para cliente ${sessionId} - Cliente no encontrado`
            );
          }
        } catch (err) {
          console.error(
            `❌ Error guardando QR para cliente ${sessionId}:`,
            err
          );
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
        const defaultPath = path.join(sessionDir, sessionId, "Default");
        if (fs.existsSync(defaultPath)) {
          const archivos = fs.readdirSync(defaultPath);
          console.log(
            `📦 Archivos guardados en 'Default' para ${sessionId}:`,
            archivos
          );
          if (archivos.length === 0) {
            console.warn(
              `⚠️ La carpeta Default está vacía para ${sessionId}, sesión podría no restaurarse después`
            );
          }
        } else {
          console.error(`❌ Carpeta Default no encontrada para ${sessionId}`);
        }

        // **NUEVO: Verificar que el cliente puede recibir mensajes**
        try {
          const isConnected = await client.isConnected();
          const connectionState = await client.getConnectionState();
          console.log(`🔍 Estado detallado de sesión ${sessionId}:`, {
            isConnected,
            connectionState,
            canReceiveMessages: true, // Asumimos que sí puede recibir mensajes si está conectado
          });
        } catch (verifyErr) {
          console.error(
            `❌ Error verificando capacidad de recepción de mensajes ${sessionId}:`,
            verifyErr.message
          );
        }

        // **NUEVO: Guardar información de sesión inmediatamente**
        try {
          await guardarInformacionSesion(sessionId, client);
        } catch (err) {
          console.error(
            `❌ Error guardando información de sesión para ${sessionId}:`,
            err.message
          );
        }
      }

      if (
        ["CONFLICT", "UNPAIRED", "UNLAUNCHED", "DISCONNECTED"].includes(state)
      ) {
        // **NUEVO: Incrementar contador de errores globales**
        if (!sessionErrors[sessionId]) sessionErrors[sessionId] = 0;
        sessionErrors[sessionId]++;

        console.log(
          `⚠️ Error ${sessionErrors[sessionId]}/3 para sesión ${sessionId}: ${state}`
        );

        // Solo intentar reconexión automática si NO se está generando QR explícitamente
        // Y no se ha alcanzado el límite de errores globales
        if (
          !permitirGuardarQR &&
          reconexionIntentos < maxIntentos &&
          sessionErrors[sessionId] < 3
        ) {
          reconexionIntentos++;
          console.log(
            `🔄 Intento ${reconexionIntentos}/${maxIntentos} de reconexión automática para ${sessionId} (error global: ${sessionErrors[sessionId]}/3)...`
          );

          // Esperar antes de intentar reconectar (tiempo progresivo)
          const tiempoEspera = 5000 * reconexionIntentos; // 5s, 10s, 15s
          await new Promise((resolve) => setTimeout(resolve, tiempoEspera));

          try {
            // Cerrar sesión actual antes de recrear
            if (sessions[sessionId]) {
              await sessions[sessionId].close();
              delete sessions[sessionId];
            }

            await crearSesion(sessionId, false); // false = NO generar QR en reconexión automática
            console.log(
              `✅ Sesión ${sessionId} reconectada automáticamente en intento ${reconexionIntentos}`
            );
          } catch (err) {
            console.error(
              `❌ Error en intento ${reconexionIntentos} de reconexión automática ${sessionId}:`,
              err.message
            );

            if (
              reconexionIntentos >= maxIntentos ||
              sessionErrors[sessionId] >= 3
            ) {
              console.error(
                `🚫 Máximo de intentos automáticos alcanzado para ${sessionId}, bloqueando por 30 minutos`
              );

              // Bloquear temporalmente esta sesión
              setTimeout(() => {
                console.log(
                  `🔓 Desbloqueando sesión ${sessionId} después de 30 minutos`
                );
                sessionErrors[sessionId] = 0;
                reconexionIntentos = 0;
              }, 30 * 60 * 1000);
            }
          }
        } else if (permitirGuardarQR) {
          console.log(
            `🔍 Sesión ${sessionId} desconectada pero está en proceso de generación de QR, no se reintenta automáticamente`
          );
        } else {
          console.error(
            `🚫 Sesión ${sessionId} desconectada permanentemente (errores: ${sessionErrors[sessionId]}/3), requiere escaneo manual de QR`
          );
        }
      }
    });

    client.onMessage(async (message) => {
      try {
        console.log(`📩 Mensaje recibido en cliente ${sessionId}:`, {
          from: message.from,
          body: message.body,
          type: message.type,
          timestamp: new Date().toISOString(),
        });

        const telefono = message.from.replace("@c.us", "");
        const mensaje = message.body;
        const cliente_id = sessionId;

        console.log(
          `🔄 Enviando al backend - Cliente: ${cliente_id}, Teléfono: ${telefono}, Mensaje: "${mensaje}"`
        );

        // Envía el mensaje al backend y espera la respuesta
        const backendResponse = await axios.post(
          "https://backend-agenda-2.onrender.com/api/webhook",
          {
            telefono,
            mensaje,
            cliente_id,
          }
        );

        console.log(`🔗 Respuesta del backend:`, {
          status: backendResponse.status,
          data: backendResponse.data,
          headers: backendResponse.headers["content-type"],
        });

        // El backend debe responder con { mensaje: "texto a enviar" }
        const respuesta = backendResponse.data && backendResponse.data.mensaje;
        if (respuesta) {
          console.log(`💬 Enviando respuesta a ${telefono}: "${respuesta}"`);
          await client.sendText(`${telefono}@c.us`, respuesta);
          console.log(`✅ Respuesta enviada exitosamente a ${telefono}`);
        } else {
          console.log(
            `⚠️ Backend no devolvió mensaje para enviar. Respuesta completa:`,
            backendResponse.data
          );
        }
      } catch (err) {
        console.error(
          "❌ Error reenviando mensaje a backend o enviando respuesta:",
          err
        );
        if (err.response) {
          console.error("❌ Respuesta del backend con error:", {
            status: err.response.status,
            data: err.response.data,
            headers: err.response.headers,
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
      console.error(
        `⚠️ ADVERTENCIA: Backend no accesible para cliente ${sessionId}. Los mensajes pueden no procesarse.`
      );
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
        await new Promise((resolve) => setTimeout(resolve, intervalo));
        tiempoEspera += intervalo;

        // Verificar si el archivo QR existe
        if (fs.existsSync(qrPath)) {
          console.log(
            `📱 Archivo QR detectado para cliente ${sessionId} después de ${tiempoEspera}ms`
          );
          break;
        }
      }

      // Verificar estado final del QR
      if (qrGuardado) {
        console.log(
          `✅ QR generado y guardado exitosamente para cliente ${sessionId}`
        );
      } else if (fs.existsSync(qrPath)) {
        console.log(
          `⚠️ Archivo QR existe pero no se confirmó guardado en DB para cliente ${sessionId}`
        );
      } else {
        console.error(
          `❌ No se pudo generar QR para cliente ${sessionId} después de ${maxEspera}ms`
        );
      }
    }

    return client;
  } catch (err) {
    console.error(`❌ Error creando sesión para ${sessionId}:`, err.message);
    console.error(`🔍 Tipo de error:`, err.name || "Unknown");

    // Log detallado del error para debugging
    if (err.stack) {
      console.error(
        `📋 Stack trace:`,
        err.stack.split("\n").slice(0, 5).join("\n")
      );
    }

    // Incrementar contador de errores para esta sesión
    sessionErrors[sessionId] = (sessionErrors[sessionId] || 0) + 1;
    console.log(
      `📊 Errores acumulados para cliente ${sessionId}: ${sessionErrors[sessionId]}/3`
    );

    // Limpiar sesión de memoria si existe (evitar estados inconsistentes)
    if (sessions[sessionId]) {
      try {
        console.log(
          `🧹 Limpiando sesión en memoria para cliente ${sessionId}...`
        );
        await sessions[sessionId].close();
      } catch (e) {
        console.log(
          `⚠️ Error cerrando sesión fallida para ${sessionId}:`,
          e.message
        );
      }
      delete sessions[sessionId];
    }

    // Si hay demasiados errores consecutivos, limpiar completamente y bloquear temporalmente
    if (sessionErrors[sessionId] >= 3) {
      console.log(
        `🚫 Cliente ${sessionId} bloqueado temporalmente por exceso de errores (${sessionErrors[sessionId]}/3)`
      );

      // Limpiar carpeta de sesión si está corrupta
      const sessionPath = path.join(sessionDir, sessionId);
      if (fs.existsSync(sessionPath)) {
        try {
          console.log(
            `🧹 Eliminando carpeta de sesión corrupta: ${sessionPath}`
          );
          fs.rmSync(sessionPath, { recursive: true, force: true });
          console.log(`✅ Carpeta eliminada para cliente ${sessionId}`);
        } catch (cleanupErr) {
          console.error(
            `❌ Error limpiando carpeta para ${sessionId}:`,
            cleanupErr.message
          );
        }
      }

      // Programar reset del contador de errores en 30 minutos
      setTimeout(() => {
        console.log(
          `� Desbloqueando y reseteando contador de errores para cliente ${sessionId}`
        );
        sessionErrors[sessionId] = 0;
      }, 30 * 60 * 1000); // 30 minutos
    }

    throw err;
  }
}

async function restaurarSesiones() {
  try {
    console.log("🔄 Iniciando restauración de sesiones...");

    // **NUEVO: Crear carpetas automáticamente ANTES de restaurar**
    await crearCarpetasAutomaticamente();

    const sessionDir =
      process.env.SESSION_FOLDER || path.join(__dirname, "tokens");

    // Verificar cuáles clientes existen en la base de datos
    let result;
    try {
      result = await pool.query("SELECT id, comercio FROM tenants ORDER BY id");
      console.log(
        `📊 Consultando base de datos... Encontrados ${result.rows.length} clientes`
      );
      if (result.rows.length > 0) {
        console.log(
          `👥 Clientes en DB:`,
          result.rows.map((r) => `${r.id}(${r.comercio || "Sin comercio"})`)
        );
      } else {
        console.log("⚠️ No se encontraron clientes en la base de datos");
        return;
      }
    } catch (err) {
      console.error("❌ Error consultando clientes de la base de datos:", err);
      return;
    }

    const clientesActivos = result.rows.map((row) => String(row.id));

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
        const appTokenFolders = fs.readdirSync("/app/tokens").filter((item) => {
          const itemPath = path.join("/app/tokens", item);
          return (
            fs.statSync(itemPath).isDirectory() &&
            !isNaN(item) &&
            clientesActivos.includes(item)
          );
        });
        console.log(
          `📂 Encontradas ${appTokenFolders.length} carpetas válidas en /app/tokens:`,
          appTokenFolders
        );

        sessionFolders = appTokenFolders.map((folder) => ({
          id: folder,
          path: path.join("/app/tokens", folder),
        }));
      } catch (err) {
        console.error("❌ Error leyendo /app/tokens:", err.message);
      }
    }

    // Si no hay carpetas en /app/tokens, buscar en sessionDir local
    if (sessionFolders.length === 0) {
      try {
        const localFolders = fs.readdirSync(sessionDir).filter((item) => {
          const itemPath = path.join(sessionDir, item);
          return (
            fs.statSync(itemPath).isDirectory() &&
            !isNaN(item) &&
            clientesActivos.includes(item)
          );
        });
        console.log(
          `📂 Encontradas ${localFolders.length} carpetas válidas en ${sessionDir}:`,
          localFolders
        );

        sessionFolders = localFolders.map((folder) => ({
          id: folder,
          path: path.join(sessionDir, folder),
        }));
      } catch (err) {
        console.error("❌ Error leyendo carpeta local:", err.message);
      }
    }
    console.log(
      `🔍 Clientes activos en BD (strings): [${clientesActivos.join(", ")}]`
    );

    // Verificar estado de sesiones activas en memoria
    const sesionesEnMemoria = Object.keys(sessions);
    console.log(
      `💾 Sesiones activas en memoria: [${sesionesEnMemoria.join(", ")}]`
    );

    // Análisis detallado de carpetas vs clientes
    const carpetasIds = sessionFolders.map((f) => f.id);
    const clientesConCarpeta = clientesActivos.filter((id) =>
      carpetasIds.includes(id)
    );
    const clientesSinCarpeta = clientesActivos.filter(
      (id) => !carpetasIds.includes(id)
    );
    const carpetasHuerfanas = carpetasIds.filter(
      (id) => !clientesActivos.includes(id)
    );

    console.log(`📋 ANÁLISIS COMPLETO:`);
    console.log(`  - Clientes con carpeta: [${clientesConCarpeta.join(", ")}]`);
    console.log(`  - Clientes sin carpeta: [${clientesSinCarpeta.join(", ")}]`);
    console.log(`  - Carpetas huérfanas: [${carpetasHuerfanas.join(", ")}]`);

    // Verificar específicamente el cliente 35
    if (clientesActivos.includes("35")) {
      console.log(`🔍 DIAGNÓSTICO CLIENTE 35:`);
      console.log(`  - Existe en BD: ✅`);
      console.log(
        `  - Tiene carpeta en disco: ${
          carpetasIds.includes("35") ? "✅" : "❌"
        }`
      );
      console.log(
        `  - Sesión en memoria: ${
          sesionesEnMemoria.includes("35") ? "✅" : "❌"
        }`
      );

      if (sessions["35"]) {
        try {
          const estado35 = await sessions["35"].getConnectionState();
          console.log(`  - Estado actual: ${estado35}`);
        } catch (e) {
          console.log(`  - Error verificando estado: ${e.message}`);
        }
      }

      // Buscar carpeta 35 manualmente en ambas ubicaciones
      const paths35 = [path.join(sessionDir, "35"), "/app/tokens/35"];

      for (const pathToCheck of paths35) {
        console.log(
          `  - Verificando ruta ${pathToCheck}: ${
            fs.existsSync(pathToCheck) ? "✅" : "❌"
          }`
        );
        if (fs.existsSync(pathToCheck)) {
          const defaultPath = path.join(pathToCheck, "Default");
          console.log(
            `    - Default folder: ${fs.existsSync(defaultPath) ? "✅" : "❌"}`
          );
          if (fs.existsSync(pathToCheck)) {
            const files = fs.readdirSync(pathToCheck);
            console.log(`    - Archivos en carpeta: [${files.join(", ")}]`);
          }
        }
      }
    }

    for (const sessionFolder of sessionFolders) {
      const clienteId =
        typeof sessionFolder === "string" ? sessionFolder : sessionFolder.id;
      const sessionPath =
        typeof sessionFolder === "string"
          ? path.join(sessionDir, sessionFolder)
          : sessionFolder.path;

      console.log(`\n🔄 Procesando cliente ${clienteId}...`);

      if (!clientesActivos.includes(clienteId)) {
        console.log(
          `⚠️ Cliente ${clienteId} no existe en DB (Clientes válidos: ${clientesActivos.join(
            ", "
          )}), saltando...`
        );
        continue;
      }

      const defaultPath = path.join(sessionPath, "Default");

      console.log(`🔍 Verificando archivos para cliente ${clienteId}:`);
      console.log(`  - Ruta sesión: ${sessionPath}`);
      console.log(
        `  - Carpeta Default: ${fs.existsSync(defaultPath) ? "✅" : "❌"}`
      );

      let tieneArchivosDeSession = false;
      let archivosEsenciales = [];
      let archivosEncontrados = [];
      let archivosNoEncontrados = [];

      if (fs.existsSync(defaultPath)) {
        const archivosDefault = fs.readdirSync(defaultPath);
        console.log(`  - Archivos en Default: [${archivosDefault.join(", ")}]`);

        archivosEsenciales = [
          "Local Storage",
          "Session Storage",
          "IndexedDB",
          "Preferences",
        ];

        for (const archivo of archivosEsenciales) {
          const existe = archivosDefault.some((archivoReal) =>
            archivoReal.toLowerCase().includes(archivo.toLowerCase())
          );
          if (existe) {
            archivosEncontrados.push(archivo);
          } else {
            archivosNoEncontrados.push(archivo);
          }
          console.log(`  - ${archivo}: ${existe ? "✅" : "❌"}`);
        }

        // 🔄 Criterio flexible: si hay al menos 1 archivo en Default, intentar restaurar
        tieneArchivosDeSession = archivosDefault.length > 0;

        if (!tieneArchivosDeSession) {
          console.log(`  - ❌ Sesión INCOMPLETA para cliente ${clienteId}`);
          console.log(
            `    - Archivos encontrados: [${archivosEncontrados.join(", ")}]`
          );
          console.log(
            `    - Archivos faltantes: [${archivosNoEncontrados.join(", ")}]`
          );
          console.log(`    - 🔄 Requiere re-autenticación con QR`);
        } else {
          console.log(
            `  - ✅ Sesión posiblemente restaurable (criterio flexible)`
          );
        }
      }

      console.log(
        `  - Tiene sesión restaurable: ${tieneArchivosDeSession ? "✅" : "❌"}`
      );

      if (fs.existsSync(defaultPath) && tieneArchivosDeSession) {
        const archivosDefault = fs.readdirSync(defaultPath);
        const tieneJsonValido = archivosDefault.some((name) =>
          name.endsWith(".json")
        );

        if (!tieneJsonValido) {
          console.log(
            `⚠️ No hay archivos .json válidos en sesión ${clienteId}, puede requerir re-autenticación manual`
          );
        }

        console.log(`🔄 Restaurando sesión para cliente ${clienteId}...`);
        console.log(`📁 Usando ruta: ${sessionPath}`);
        try {
          const originalSessionFolder = process.env.SESSION_FOLDER;
          process.env.SESSION_FOLDER = path.dirname(sessionPath);

          await crearSesion(clienteId, false);
          console.log(`✅ Sesión restaurada para cliente ${clienteId}`);

          if (originalSessionFolder) {
            process.env.SESSION_FOLDER = originalSessionFolder;
          } else {
            delete process.env.SESSION_FOLDER;
          }

          await new Promise((resolve) => setTimeout(resolve, 3000));
        } catch (err) {
          console.error(
            `❌ Error restaurando sesión ${clienteId}:`,
            err.message
          );
        }
      } else {
        console.log(
          `⚠️ No hay datos de sesión válidos para cliente ${clienteId} en ${sessionPath}`
        );
        console.log(`🔍 Razones posibles:`);
        console.log(
          `   - Carpeta Default existe: ${fs.existsSync(defaultPath)}`
        );
        console.log(
          `   - Tiene archivos de sesión: ${tieneArchivosDeSession || "false"}`
        );
        if (fs.existsSync(sessionPath)) {
          console.log(
            `   - Archivos en carpeta raíz: [${fs
              .readdirSync(sessionPath)
              .join(", ")}]`
          );
        } else {
          console.log(`   - Carpeta de sesión no existe`);
        }
      }
    }

    console.log("✅ Proceso de restauración completado");

    // **NUEVA FUNCIONALIDAD: Limpiar carpetas huérfanas y SingletonLocks**
    console.log("🧹 Limpiando carpetas huérfanas y archivos de bloqueo...");

    // Buscar y eliminar carpetas de clientes que ya no están en la BD
    const searchPaths = [sessionDir, "/app/tokens"];

    for (const searchPath of searchPaths) {
      if (!fs.existsSync(searchPath)) continue;

      try {
        const allFolders = fs.readdirSync(searchPath).filter((item) => {
          const itemPath = path.join(searchPath, item);
          return fs.statSync(itemPath).isDirectory() && !isNaN(item); // Solo carpetas numéricas
        });

        const huerfanas = allFolders.filter(
          (folder) => !clientesActivos.includes(folder)
        );

        if (huerfanas.length > 0) {
          console.log(
            `🗑️ Eliminando ${huerfanas.length} carpetas huérfanas de ${searchPath}:`,
            huerfanas
          );

          for (const huerfana of huerfanas) {
            const carpetaPath = path.join(searchPath, huerfana);
            try {
              // Eliminar archivos SingletonLock específicamente antes de eliminar la carpeta
              const singletonLockPath = path.join(carpetaPath, "SingletonLock");
              if (fs.existsSync(singletonLockPath)) {
                fs.unlinkSync(singletonLockPath);
                console.log(
                  `🔓 Eliminado SingletonLock de cliente ${huerfana}`
                );
              }

              // Eliminar toda la carpeta
              fs.rmSync(carpetaPath, { recursive: true, force: true });
              console.log(`✅ Carpeta huérfana eliminada: ${huerfana}`);
            } catch (err) {
              console.error(
                `❌ Error eliminando carpeta ${huerfana}:`,
                err.message
              );
            }
          }
        } else {
          console.log(`✅ No hay carpetas huérfanas en ${searchPath}`);
        }
      } catch (err) {
        console.error(
          `❌ Error durante limpieza en ${searchPath}:`,
          err.message
        );
      }
    }

    // Limpiar SingletonLocks de clientes activos también (por si acaso)
    for (const clienteId of clientesActivos) {
      for (const searchPath of searchPaths) {
        const singletonPath = path.join(searchPath, clienteId, "SingletonLock");
        if (fs.existsSync(singletonPath)) {
          try {
            fs.unlinkSync(singletonPath);
            console.log(
              `🔓 Limpiado SingletonLock para cliente activo ${clienteId}`
            );
          } catch (err) {
            console.error(
              `❌ Error limpiando SingletonLock ${clienteId}:`,
              err.message
            );
          }
        }
      }
    }

    // Mostrar clientes activos que NO tienen carpetas de sesión (solo informativo)
    console.log("🔍 Verificando clientes activos sin carpetas de sesión...");
    const carpetasExistentes = sessionFolders.map((sf) =>
      typeof sf === "string" ? sf : sf.id
    );
    const clientesSinCarpetas = clientesActivos.filter(
      (id) => !carpetasExistentes.includes(id)
    );

    if (clientesSinCarpetas.length > 0) {
      console.log(
        `📋 Clientes sin carpetas de sesión (requieren QR manual):`,
        clientesSinCarpetas
      );
      console.log(`📱 Para generar QR manualmente, usa los endpoints:`);
      clientesSinCarpetas.forEach((id) => {
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

// **NUEVA FUNCIÓN: Guardar información esencial de sesión para restauración futura**
async function guardarInformacionSesion(clienteId, client) {
  console.log(
    `💾 Guardando información de sesión para cliente ${clienteId}...`
  );

  try {
    const sessionDir =
      process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const productionPath = "/app/tokens";

    // Usar la ruta de producción si existe, sino la local
    const basePath = fs.existsSync(productionPath)
      ? productionPath
      : sessionDir;
    const clientePath = path.join(basePath, clienteId);
    const defaultPath = path.join(clientePath, "Default");

    // Asegurar que las carpetas existen
    if (!fs.existsSync(clientePath)) {
      fs.mkdirSync(clientePath, { recursive: true });
    }
    if (!fs.existsSync(defaultPath)) {
      fs.mkdirSync(defaultPath, { recursive: true });
    }

    // Obtener información del dispositivo/conexión
    let deviceInfo = {};
    try {
      const hostDevice = await client.getHostDevice();
      deviceInfo = {
        platform: hostDevice.platform || "unknown",
        phone: hostDevice.phone || {},
        connected: true,
        lastSeen: new Date().toISOString(),
      };
      console.log(`📱 Información del dispositivo obtenida para ${clienteId}`);
    } catch (err) {
      console.log(
        `⚠️ No se pudo obtener info del dispositivo para ${clienteId}: ${err.message}`
      );
      deviceInfo = {
        connected: true,
        lastSeen: new Date().toISOString(),
        fallback: true,
      };
    }

    // Guardar información de sesión en archivo JSON
    const sessionInfoFile = path.join(defaultPath, "SessionInfo.json");
    const sessionInfo = {
      clienteId: clienteId,
      connectedAt: new Date().toISOString(),
      deviceInfo: deviceInfo,
      sessionVersion: "1.0",
      isReady: true,
      lastUpdate: new Date().toISOString(),
    };

    fs.writeFileSync(sessionInfoFile, JSON.stringify(sessionInfo, null, 2));
    console.log(`💾 Información de sesión guardada: ${sessionInfoFile}`);

    // Actualizar también las preferencias de Chrome con info actualizada
    const preferencesFile = path.join(defaultPath, "Preferences");
    let preferences = {};

    try {
      if (fs.existsSync(preferencesFile)) {
        preferences = JSON.parse(fs.readFileSync(preferencesFile, "utf8"));
      }
    } catch (err) {
      console.log(
        `⚠️ Error leyendo preferencias existentes, creando nuevas: ${err.message}`
      );
      preferences = {};
    }

    // Actualizar preferencias con información de conexión
    preferences.whatsapp = {
      ...preferences.whatsapp,
      client_id: clienteId,
      last_connected: new Date().toISOString(),
      device_info: deviceInfo,
      session_ready: true,
    };

    preferences.profile = {
      ...preferences.profile,
      name: `WhatsApp-${clienteId}`,
      default_content_setting_values: {
        notifications: 1,
      },
    };

    fs.writeFileSync(preferencesFile, JSON.stringify(preferences, null, 2));
    console.log(`⚙️ Preferencias actualizadas para cliente ${clienteId}`);

    console.log(
      `✅ Información de sesión guardada exitosamente para cliente ${clienteId}`
    );
  } catch (err) {
    console.error(
      `❌ Error guardando información de sesión para ${clienteId}:`,
      err
    );
    throw err;
  }
}

app.get("/iniciar/:clienteId", async (req, res) => {
  const { clienteId } = req.params;
  try {
    console.log(`🚀 Iniciando sesión para cliente ${clienteId}...`);
    await crearSesionConTimeout(clienteId, 60000, true); // true para guardar QR

    // Verificar que el QR se haya generado
    const sessionDir =
      process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const qrPath = path.join(sessionDir, `${clienteId}.html`);

    if (fs.existsSync(qrPath)) {
      console.log(`✅ QR generado para cliente ${clienteId}`);
      res.send(
        `✅ Sesión iniciada para ${clienteId}. Escaneá el QR en /qr/${clienteId}`
      );
    } else {
      console.log(
        `⚠️ Sesión creada pero QR no encontrado para cliente ${clienteId}`
      );
      res.send(
        `⚠️ Sesión iniciada para ${clienteId}, pero QR aún no disponible. Reintenta en /qr/${clienteId} en unos segundos.`
      );
    }
  } catch (error) {
    console.error(
      `❌ Error al iniciar sesión para cliente ${clienteId}:`,
      error
    );
    res.status(500).send(`Error al iniciar sesión: ${error.message}`);
  }
});

app.get("/qr/:clienteId", (req, res) => {
  const clienteId = req.params.clienteId;
  const sessionDir =
    process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
  const filePath = path.join(sessionDir, `${clienteId}.html`);
  if (fs.existsSync(filePath)) {
    res.sendFile(filePath);
  } else {
    res
      .status(404)
      .send(`<h2>⚠️ Aún no se generó un QR para el cliente: ${clienteId}</h2>`);
  }
});

app.get("/debug/sesiones", async (req, res) => {
  try {
    const diagnostico = {
      timestamp: new Date().toISOString(),
      sesiones_activas: Object.keys(sessions).length,
      backend_disponible: await verificarConectividadBackend(),
      sesiones: {},
    };

    for (const [clienteId, session] of Object.entries(sessions)) {
      try {
        const isConnected = await session.isConnected();
        const connectionState = await session.getConnectionState();

        diagnostico.sesiones[clienteId] = {
          conectado: isConnected,
          estado: connectionState,
          errores_acumulados: sessionErrors[clienteId] || 0,
          bloqueado: (sessionErrors[clienteId] || 0) >= 3,
          tipo_sesion: typeof session,
          tiene_onMessage: typeof session.onMessage === "function",
        };

        // Verificar si puede obtener información del dispositivo
        try {
          const hostDevice = await session.getHostDevice();
          diagnostico.sesiones[clienteId].dispositivo = {
            platform: hostDevice.platform,
            phone: hostDevice.phone,
            connected: hostDevice.connected,
          };
        } catch (deviceErr) {
          diagnostico.sesiones[
            clienteId
          ].dispositivo = `Error: ${deviceErr.message}`;
        }
      } catch (err) {
        diagnostico.sesiones[clienteId] = {
          error: err.message,
          tipo_error: err.name,
        };
      }
    }

    res.json(diagnostico);
  } catch (err) {
    console.error("❌ Error en diagnóstico de sesiones:", err);
    res.status(500).json({ error: "Error interno", details: err.message });
  }
});

app.get("/diagnostico/:clienteId?", async (req, res) => {
  const { clienteId } = req.params;
  const sessionDir =
    process.env.SESSION_FOLDER || path.join(__dirname, "tokens");

  try {
    const diagnostico = {
      timestamp: new Date().toISOString(),
      session_folder: sessionDir,
      clientes_solicitados: clienteId ? [clienteId] : "todos",
    };

    // Si se especifica un cliente, hacer diagnóstico detallado
    if (clienteId) {
      const sessionPath = path.join(sessionDir, clienteId);
      const defaultPath = path.join(sessionPath, "Default");

      diagnostico.cliente = {
        id: clienteId,
        carpeta_existe: fs.existsSync(sessionPath),
        carpeta_default_existe: fs.existsSync(defaultPath),
        sesion_activa: !!sessions[clienteId],
        errores_acumulados: sessionErrors[clienteId] || 0,
        bloqueado: (sessionErrors[clienteId] || 0) >= 3,
      };

      if (fs.existsSync(defaultPath)) {
        const archivos = fs.readdirSync(defaultPath);
        const archivosCriticos = [
          "Local Storage",
          "Preferences",
          "IndexedDB",
          "Session Storage",
        ];

        diagnostico.cliente.archivos = {
          total_archivos: archivos.length,
          archivos_encontrados: archivos,
          archivos_criticos: {},
        };

        archivosCriticos.forEach((archivo) => {
          const existe = archivos.some((a) =>
            a.toLowerCase().includes(archivo.toLowerCase())
          );
          diagnostico.cliente.archivos.archivos_criticos[archivo] = existe;
        });

        // Evaluar si es restaurable
        const tieneLocalStorage = archivos.some((a) =>
          a.toLowerCase().includes("local storage")
        );
        const tieneOtros = archivos.some(
          (a) =>
            a.toLowerCase().includes("preferences") ||
            a.toLowerCase().includes("indexeddb")
        );
        diagnostico.cliente.es_restaurable = tieneLocalStorage && tieneOtros;
      } else {
        diagnostico.cliente.es_restaurable = false;
        diagnostico.cliente.razon = "Carpeta Default no existe";
      }

      // Estado de conexión si está activa
      if (sessions[clienteId]) {
        try {
          const isConnected = await sessions[clienteId].isConnected();
          diagnostico.cliente.estado_conexion = isConnected
            ? "CONNECTED"
            : "DISCONNECTED";
        } catch (err) {
          diagnostico.cliente.estado_conexion = "ERROR";
          diagnostico.cliente.error_conexion = err.message;
        }
      } else {
        diagnostico.cliente.estado_conexion = "NO_ACTIVE";
      }
    } else {
      // Diagnóstico general de todos los clientes
      const result = await pool.query(
        "SELECT id, comercio FROM tenants ORDER BY id"
      );
      diagnostico.resumen = {
        clientes_db: result.rows.length,
        sesiones_activas: Object.keys(sessions).length,
        sesiones_con_errores: Object.keys(sessionErrors).filter(
          (id) => sessionErrors[id] > 0
        ).length,
        sesiones_bloqueadas: Object.keys(sessionErrors).filter(
          (id) => sessionErrors[id] >= 3
        ).length,
      };

      diagnostico.clientes = [];

      for (const row of result.rows) {
        const id = String(row.id);
        const sessionPath = path.join(sessionDir, id);
        const defaultPath = path.join(sessionPath, "Default");

        const clienteInfo = {
          id: id,
          comercio: row.comercio,
          carpeta_existe: fs.existsSync(sessionPath),
          carpeta_default_existe: fs.existsSync(defaultPath),
          sesion_activa: !!sessions[id],
          errores_acumulados: sessionErrors[id] || 0,
          bloqueado: (sessionErrors[id] || 0) >= 3,
          es_restaurable: false,
        };

        if (fs.existsSync(defaultPath)) {
          const archivos = fs.readdirSync(defaultPath);
          const tieneLocalStorage = archivos.some((a) =>
            a.toLowerCase().includes("local storage")
          );
          const tieneOtros = archivos.some(
            (a) =>
              a.toLowerCase().includes("preferences") ||
              a.toLowerCase().includes("indexeddb")
          );
          clienteInfo.es_restaurable = tieneLocalStorage && tieneOtros;
          clienteInfo.total_archivos = archivos.length;
        }

        diagnostico.clientes.push(clienteInfo);
      }
    }

    res.json(diagnostico);
  } catch (err) {
    console.error("❌ Error en diagnóstico:", err);
    res
      .status(500)
      .json({ error: "Error generando diagnóstico", details: err.message });
  }
});

app.post("/enviar-mensaje", async (req, res) => {
  const { cliente_id, telefono, mensaje } = req.body;
  const session = sessions[String(cliente_id)];
  if (!session)
    return res
      .status(404)
      .json({ error: "Sesión no encontrada para este cliente" });

  try {
    const state = await session.getConnectionState();
    if (state !== "CONNECTED")
      return res
        .status(400)
        .json({ error: `Sesión no conectada (estado: ${state})` });
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
    const sessionDir =
      process.env.SESSION_FOLDER || path.join(__dirname, "tokens");

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
          const estadoRaw = await sessions[clienteId].getConnectionState();
          const isConnected = await sessions[clienteId].isConnected();

          // Normalizar estado según lo que espera el admin panel
          if (estadoRaw === "CONNECTED" || isConnected === true) {
            estado = "CONNECTED";
          } else if (
            estadoRaw === "DISCONNECTED" ||
            estadoRaw === "UNPAIRED" ||
            estadoRaw === "UNLAUNCHED"
          ) {
            estado = "DISCONNECTED";
          } else if (estadoRaw === "TIMEOUT") {
            estado = "TIMEOUT";
          } else {
            // Para cualquier otro estado, mostrar como desconectado pero con el valor real
            estado = estadoRaw || "DISCONNECTED";
          }

          console.log(
            `📊 Cliente ${clienteId}: estadoRaw="${estadoRaw}", isConnected=${isConnected}, estadoFinal="${estado}"`
          );
        } catch (err) {
          estado = "ERROR";
          console.error(
            `❌ Error obteniendo estado de ${clienteId}:`,
            err.message
          );
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
        enMemoria: !!sessions[clienteId],
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
    console.log(
      `🔄 Forzando restauración de sesión para cliente ${clienteId}...`
    );

    const sessionDir =
      process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    let sessionPath = path.join(sessionDir, clienteId);

    // Si no existe en la ruta local, buscar en /app/tokens
    if (
      !fs.existsSync(sessionPath) &&
      fs.existsSync(`/app/tokens/${clienteId}`)
    ) {
      sessionPath = `/app/tokens/${clienteId}`;
      console.log(`📁 Usando ruta alternativa: ${sessionPath}`);
    }

    if (!fs.existsSync(sessionPath)) {
      return res.status(404).json({
        error: "No se encontraron archivos de sesión para este cliente",
        requiereQR: true,
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
          await new Promise((resolve) => setTimeout(resolve, 3000));
          estado = await sessions[clienteId].getConnectionState();
        } catch (err) {
          estado = "ERROR";
        }
      }

      res.json({
        success: true,
        mensaje: `Sesión restaurada para cliente ${clienteId}`,
        estado: estado,
        rutaUsada: sessionPath,
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
      details: error.message,
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
      venom: {},
    };

    // 1. Verificar en base de datos
    try {
      const result = await pool.query(
        "SELECT id, nombre, comercio FROM tenants WHERE id = $1",
        [clienteId]
      );
      diagnostico.baseDatos = {
        existe: result.rows.length > 0,
        datos: result.rows[0] || null,
      };
    } catch (err) {
      diagnostico.baseDatos = { error: err.message };
    }

    // 2. Verificar carpetas en disco
    const sessionDir =
      process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const rutasVerificar = [
      path.join(sessionDir, clienteId),
      `/app/tokens/${clienteId}`,
    ];

    diagnostico.carpetas.rutas = {};
    for (const ruta of rutasVerificar) {
      const existe = fs.existsSync(ruta);
      diagnostico.carpetas.rutas[ruta] = {
        existe,
        archivos: existe ? fs.readdirSync(ruta) : [],
        defaultFolder: existe
          ? fs.existsSync(path.join(ruta, "Default"))
          : false,
      };

      if (existe) {
        const defaultPath = path.join(ruta, "Default");
        if (fs.existsSync(defaultPath)) {
          diagnostico.carpetas.rutas[ruta].defaultFiles =
            fs.readdirSync(defaultPath);

          // Verificar archivos importantes de WhatsApp
          const archivosImportantes = [
            "Local Storage",
            "Session Storage",
            "IndexedDB",
            "Preferences",
          ];

          diagnostico.carpetas.rutas[ruta].archivosImportantes = {};
          for (const archivo of archivosImportantes) {
            const archivoPath = path.join(defaultPath, archivo);
            diagnostico.carpetas.rutas[ruta].archivosImportantes[archivo] =
              fs.existsSync(archivoPath);
          }
        }
      }
    }

    // 3. Verificar sesión en memoria
    diagnostico.sesionMemoria = {
      existe: !!sessions[clienteId],
      tipo: sessions[clienteId] ? typeof sessions[clienteId] : null,
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
      const carpetaEncontrada = rutasVerificar.find((ruta) =>
        fs.existsSync(ruta)
      );

      if (carpetaEncontrada) {
        diagnostico.venom = {
          carpetaDetectada: carpetaEncontrada,
          puedeRestaurar: fs.existsSync(
            path.join(carpetaEncontrada, "Default")
          ),
          configuracion: {
            session: clienteId,
            userDataDir: carpetaEncontrada,
            browserArgs: [
              "--no-sandbox",
              "--disable-setuid-sandbox",
              "--disable-dev-shm-usage",
            ],
          },
        };
      } else {
        diagnostico.venom = {
          carpetaDetectada: null,
          puedeRestaurar: false,
          requiereQR: true,
        };
      }
    } catch (err) {
      diagnostico.venom = { error: err.message };
    }

    console.log(
      `📋 Diagnóstico completo para cliente ${clienteId}:`,
      JSON.stringify(diagnostico, null, 2)
    );

    res.json(diagnostico);
  } catch (error) {
    console.error(`❌ Error en diagnóstico de cliente ${clienteId}:`, error);
    res.status(500).json({ error: error.message });
  }
});

app.delete("/limpiar-huerfanas", async (req, res) => {
  try {
    const sessionDir =
      process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    if (!fs.existsSync(sessionDir)) {
      return res.json({
        mensaje: "No existe carpeta de sesiones",
        eliminadas: [],
      });
    }

    // Obtener clientes válidos de la DB
    const result = await pool.query("SELECT id FROM tenants");
    const clientesValidos = result.rows.map((r) => String(r.id));

    // Encontrar carpetas huérfanas
    const carpetas = fs.readdirSync(sessionDir).filter((item) => {
      const itemPath = path.join(sessionDir, item);
      return fs.statSync(itemPath).isDirectory() && !isNaN(item);
    });

    const carpetasHuerfanas = carpetas.filter(
      (c) => !clientesValidos.includes(c)
    );
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
      clientes_validos: clientesValidos,
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
      const result = await pool.query(
        "SELECT id, comercio FROM tenants WHERE id = $1",
        [clienteId]
      );
      if (result.rows.length === 0) {
        return res.status(404).json({
          error: `Cliente ${clienteId} no existe en la base de datos`,
          accion: "verificar_cliente",
        });
      }
      console.log(
        `✅ Cliente ${clienteId} encontrado en BD: ${result.rows[0].comercio}`
      );
    } catch (dbError) {
      console.error(`❌ Error verificando cliente en BD: ${dbError.message}`);
      // Continuar anyway si hay problemas de BD
    }

    // 2. Cerrar sesión existente si está en memoria
    if (sessions[clienteId]) {
      console.log(
        `🔒 Cerrando sesión existente en memoria para ${clienteId}...`
      );
      try {
        await sessions[clienteId].close();
        console.log(`✅ Sesión en memoria cerrada para ${clienteId}`);
      } catch (closeError) {
        console.log(`⚠️ Error cerrando sesión: ${closeError.message}`);
      }
      delete sessions[clienteId];
    }

    // 3. Limpiar archivos de sesión existentes si los hay
    const sessionDir =
      process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const rutasLimpiar = [
      path.join(sessionDir, clienteId),
      `/app/tokens/${clienteId}`,
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
      await pool.query("UPDATE tenants SET qr_code = NULL WHERE id = $1", [
        clienteId,
      ]);
      console.log(`✅ QR limpiado en base de datos para cliente ${clienteId}`);
    } catch (dbError) {
      console.log(`⚠️ Error limpiando QR en BD: ${dbError.message}`);
    }

    // 5. Crear nueva sesión desde cero
    console.log(
      `🚀 Creando nueva sesión desde cero para cliente ${clienteId}...`
    );

    try {
      await crearSesionConTimeout(clienteId, 45000, true); // 45 segundos timeout, generar QR

      // Verificar que el QR se haya generado
      const sessionDir =
        process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
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
        const qrCheck = await pool.query(
          "SELECT qr_code FROM tenants WHERE id = $1",
          [clienteId]
        );
        if (qrCheck.rows.length > 0 && qrCheck.rows[0].qr_code) {
          qrEnDB = true;
          console.log(
            `✅ QR guardado en base de datos para cliente ${clienteId}`
          );
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
          ruta_qr: qrPath,
        },
      };

      console.log(
        `✅ Nueva sesión creada exitosamente para cliente ${clienteId}`
      );
      res.json(response);
    } catch (createError) {
      console.error(`❌ Error creando nueva sesión: ${createError.message}`);
      res.status(500).json({
        error: "Error creando nueva sesión",
        details: createError.message,
        accion: "reintentar",
        qr_url: `/qr/${clienteId}`, // Intentar mostrar QR anyway
      });
    }
  } catch (error) {
    console.error(`❌ Error en forzar-nueva-sesion para ${clienteId}:`, error);
    res.status(500).json({
      error: "Error interno del servidor",
      details: error.message,
    });
  }
});

app.post("/test-mensaje/:clienteId", async (req, res) => {
  const { clienteId } = req.params;
  const { telefono, mensaje } = req.body;

  if (!sessions[clienteId]) {
    return res.status(404).json({ error: "Sesión no encontrada" });
  }

  try {
    console.log(
      `🧪 Enviando mensaje de prueba desde cliente ${clienteId} a ${telefono}: "${mensaje}"`
    );

    // Verificar estado de la sesión
    const isConnected = await sessions[clienteId].isConnected();
    const connectionState = await sessions[clienteId].getConnectionState();

    console.log(`📊 Estado de sesión ${clienteId}:`, {
      isConnected,
      connectionState,
    });

    if (!isConnected) {
      return res.status(400).json({
        error: "Sesión no conectada",
        estado: connectionState,
        conectado: isConnected,
      });
    }

    // Simular mensaje recibido (para probar el handler)
    const fakeMessage = {
      from: `${telefono}@c.us`,
      body: mensaje,
      type: "chat",
    };

    console.log(`🎭 Simulando mensaje recibido:`, fakeMessage);

    // Llamar manualmente al handler
    try {
      const backendResponse = await axios.post(
        "https://backend-agenda-2.onrender.com/api/webhook",
        {
          telefono,
          mensaje,
          cliente_id: clienteId,
        }
      );

      console.log(`🔗 Respuesta del backend para test:`, backendResponse.data);

      const respuesta = backendResponse.data && backendResponse.data.mensaje;
      if (respuesta) {
        console.log(
          `💬 Enviando respuesta de prueba a ${telefono}: "${respuesta}"`
        );
        await sessions[clienteId].sendText(`${telefono}@c.us`, respuesta);
        console.log(`✅ Mensaje de prueba enviado exitosamente`);

        res.json({
          success: true,
          mensaje: "Mensaje de prueba enviado exitosamente",
          respuesta_backend: backendResponse.data,
          respuesta_enviada: respuesta,
        });
      } else {
        res.json({
          success: false,
          mensaje: "Backend no devolvió respuesta",
          respuesta_backend: backendResponse.data,
        });
      }
    } catch (backendErr) {
      console.error("❌ Error en test del backend:", backendErr.message);
      res.status(500).json({
        error: "Error comunicándose con el backend",
        details: backendErr.message,
        response: backendErr.response ? backendErr.response.data : null,
      });
    }
  } catch (err) {
    console.error(
      `❌ Error en test de mensaje para cliente ${clienteId}:`,
      err
    );
    res.status(500).json({ error: "Error interno", details: err.message });
  }
});

app.post("/notificar-chat-humano", async (req, res) => {
  try {
    const { cliente_id, telefono, mensaje, tipo } = req.body;

    if (!cliente_id || !telefono) {
      return res
        .status(400)
        .json({ error: "cliente_id y telefono son requeridos" });
    }

    console.log(`🚨 ==========================================`);
    console.log(`🚨 ALERTA: ATENCIÓN HUMANA REQUERIDA`);
    console.log(`🚨 ==========================================`);
    console.log(`📞 Cliente ID: ${cliente_id}`);
    console.log(`📱 Teléfono: ${telefono}`);
    console.log(`💬 Último mensaje: ${mensaje}`);
    console.log(`🔔 Tipo: ${tipo || "solicitud_ayuda"}`);
    console.log(`⏰ Fecha: ${new Date().toLocaleString("es-AR")}`);

    // Buscar información del cliente en la base de datos
    let comercio = "N/A";
    let nombre = "N/A";
    try {
      const clienteInfo = await pool.query(
        "SELECT comercio, nombre FROM tenants WHERE id = $1",
        [cliente_id]
      );
      if (clienteInfo.rows.length > 0) {
        comercio = clienteInfo.rows[0].comercio || "N/A";
        nombre = clienteInfo.rows[0].nombre || "N/A";
        console.log(`🏢 Comercio: ${comercio}`);
        console.log(`👤 Contacto: ${nombre}`);
      }
    } catch (err) {
      console.log(`⚠️ No se pudo obtener info del cliente: ${err.message}`);
    }

    console.log(`🚨 ==========================================`);
    console.log(
      `💡 El usuario puede escribir "Bot" para volver al asistente virtual`
    );
    console.log(`🚨 ==========================================`);

    // Nota: Autonotificación removida como se solicitó
    console.log(
      `ℹ️ Notificación registrada. El administrador debe monitorear manualmente las solicitudes de ayuda.`
    );

    res.json({
      success: true,
      mensaje: "Notificación de chat humano registrada",
      cliente_id,
      telefono,
      action: "logged_only",
      nota: "Autonotificación deshabilitada",
    });
  } catch (error) {
    console.error("❌ Error procesando notificación de chat humano:", error);
    res
      .status(500)
      .json({ error: "Error procesando notificación", details: error.message });
  }
});

// Endpoint de salud para verificar estado de sesiones
app.get("/health", async (req, res) => {
  try {
    const estadoSesiones = {};

    for (const clienteId in sessions) {
      try {
        const isConnected = await verificarEstadoSesion(clienteId);
        const state = sessions[clienteId]
          ? await sessions[clienteId].getConnectionState()
          : "NO_SESSION";

        estadoSesiones[clienteId] = {
          conectada: isConnected,
          estado: state,
          existe: !!sessions[clienteId],
        };
      } catch (e) {
        estadoSesiones[clienteId] = {
          conectada: false,
          estado: "ERROR",
          existe: !!sessions[clienteId],
          error: e.message,
        };
      }
    }

    const totalSesiones = Object.keys(sessions).length;
    const sesionesConectadas = Object.values(estadoSesiones).filter(
      (s) => s.conectada
    ).length;

    res.json({
      status: "healthy",
      timestamp: new Date().toISOString(),
      sesiones: {
        total: totalSesiones,
        conectadas: sesionesConectadas,
        desconectadas: totalSesiones - sesionesConectadas,
        detalle: estadoSesiones,
      },
    });
  } catch (error) {
    res.status(500).json({
      status: "unhealthy",
      error: error.message,
      timestamp: new Date().toISOString(),
    });
  }
});

// Endpoint para reconectar una sesión específica
app.post("/reconectar/:clienteId", async (req, res) => {
  const clienteId = req.params.clienteId;

  try {
    console.log(`🔄 Solicitud manual de reconexión para sesión ${clienteId}`);

    // Verificar que el cliente existe en la DB
    const cliente = await pool.query(
      "SELECT id, comercio FROM tenants WHERE id = $1",
      [clienteId]
    );
    if (cliente.rows.length === 0) {
      return res
        .status(404)
        .json({ error: "Cliente no encontrado en la base de datos" });
    }

    await reconectarSesion(clienteId);

    res.json({
      success: true,
      message: `Reconexión iniciada para sesión ${clienteId}`,
      cliente: cliente.rows[0].comercio,
    });
  } catch (error) {
    console.error(`❌ Error en reconexión manual ${clienteId}:`, error);
    res.status(500).json({
      error: "Error iniciando reconexión",
      details: error.message,
    });
  }
});

app.post("/generar-qr/:clienteId", async (req, res) => {
  const { clienteId } = req.params;

  try {
    console.log(
      `🎯 Solicitud específica de generación QR para cliente ${clienteId}...`
    );

    // 1. Verificar que el cliente existe en la base de datos
    try {
      const result = await pool.query(
        "SELECT id, comercio FROM tenants WHERE id = $1",
        [clienteId]
      );
      if (result.rows.length === 0) {
        return res.status(404).json({
          error: `Cliente ${clienteId} no existe en la base de datos`,
          success: false,
        });
      }
      console.log(
        `✅ Cliente ${clienteId} encontrado: ${result.rows[0].comercio}`
      );
    } catch (dbError) {
      console.error(`❌ Error verificando cliente en BD: ${dbError.message}`);
      return res
        .status(500)
        .json({ error: "Error verificando cliente en base de datos" });
    }

    // 2. Resetear contador de errores para permitir nueva generación
    sessionErrors[clienteId] = 0;

    // 3. Cerrar sesión existente si está en memoria
    if (sessions[clienteId]) {
      console.log(
        `🔒 Cerrando sesión existente para regenerar QR ${clienteId}...`
      );
      try {
        await sessions[clienteId].close();
        console.log(`✅ Sesión cerrada para ${clienteId}`);
      } catch (closeError) {
        console.log(`⚠️ Error cerrando sesión: ${closeError.message}`);
      }
      delete sessions[clienteId];
    }

    // 4. Limpiar archivos de sesión existentes
    const sessionDir =
      process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const rutasLimpiar = [
      path.join(sessionDir, clienteId),
      `/app/tokens/${clienteId}`,
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
      const qrCheck = await pool.query(
        "SELECT qr_code FROM tenants WHERE id = $1",
        [clienteId]
      );
      if (qrCheck.rows.length > 0 && qrCheck.rows[0].qr_code) {
        qrEnDB = true;
        console.log(
          `✅ QR guardado en base de datos para cliente ${clienteId}`
        );
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
        ruta_qr: qrPath,
      },
    });
  } catch (error) {
    console.error(`❌ Error generando QR para ${clienteId}:`, error);
    res.status(500).json({
      success: false,
      error: "Error generando QR",
      details: error.message,
      cliente_id: clienteId,
    });
  }
});

app.post("/reset-errores/:clienteId", async (req, res) => {
  const { clienteId } = req.params;

  try {
    const erroresAnteriores = sessionErrors[clienteId] || 0;
    sessionErrors[clienteId] = 0;

    console.log(
      `🔄 Contador de errores reseteado para cliente ${clienteId} (era: ${erroresAnteriores})`
    );

    res.json({
      success: true,
      mensaje: `Contador de errores reseteado para cliente ${clienteId}`,
      errores_anteriores: erroresAnteriores,
      cliente_id: clienteId,
    });
  } catch (error) {
    console.error(`❌ Error reseteando errores para ${clienteId}:`, error);
    res.status(500).json({
      success: false,
      error: "Error reseteando contador de errores",
      details: error.message,
    });
  }
});

app.post("/limpiar-locks/:clienteId", async (req, res) => {
  const { clienteId } = req.params;

  try {
    console.log(
      `🧹 Limpiando archivos de bloqueo para cliente ${clienteId}...`
    );

    const sessionDir =
      process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const sessionPath = path.join(sessionDir, clienteId);
    const singletonLockPath = path.join(sessionPath, "SingletonLock");

    let archivosLimpiados = [];

    // Limpiar SingletonLock
    if (fs.existsSync(singletonLockPath)) {
      try {
        fs.unlinkSync(singletonLockPath);
        archivosLimpiados.push("SingletonLock");
        console.log(
          `🔓 Archivo SingletonLock eliminado para cliente ${clienteId}`
        );
      } catch (e) {
        console.log(`⚠️ Error eliminando SingletonLock: ${e.message}`);
      }
    }

    // También buscar en /app/tokens si es diferente
    if (fs.existsSync(`/app/tokens/${clienteId}`)) {
      const altSingletonPath = path.join(
        `/app/tokens/${clienteId}`,
        "SingletonLock"
      );
      if (fs.existsSync(altSingletonPath)) {
        try {
          fs.unlinkSync(altSingletonPath);
          archivosLimpiados.push("SingletonLock (alternativo)");
          console.log(
            `🔓 Archivo SingletonLock alternativo eliminado para cliente ${clienteId}`
          );
        } catch (e) {
          console.log(
            `⚠️ Error eliminando SingletonLock alternativo: ${e.message}`
          );
        }
      }
    }

    res.json({
      success: true,
      mensaje: `Archivos de bloqueo limpiados para cliente ${clienteId}`,
      archivos_limpiados: archivosLimpiados,
      cliente_id: clienteId,
    });
  } catch (error) {
    console.error(`❌ Error limpiando locks para ${clienteId}:`, error);
    res.status(500).json({
      success: false,
      error: "Error limpiando archivos de bloqueo",
      details: error.message,
    });
  }
});

app.get("/debug/errores", (req, res) => {
  res.json({
    session_errors: sessionErrors,
    total_clientes_con_errores: Object.keys(sessionErrors).length,
    clientes_bloqueados: Object.entries(sessionErrors)
      .filter(([clienteId, errores]) => errores >= 5)
      .map(([clienteId, errores]) => ({ clienteId, errores })),
  });
});

// **NUEVO ENDPOINT DE DEBUG DETALLADO**
app.get("/debug/estados", async (req, res) => {
  const debug = {
    sesiones_memoria: {},
    tenants_bd: [],
    diagnostico_detallado: [],
  };

  try {
    // Información de sesiones en memoria
    for (const [clienteId, session] of Object.entries(sessions)) {
      debug.sesiones_memoria[clienteId] = {
        existe: !!session,
        tipo: typeof session,
        es_objeto: session && typeof session === "object",
      };

      if (session) {
        try {
          // Probar diferentes métodos para verificar estado
          const estado = await session.getConnectionState();
          const isConnected = await session.isConnected();

          debug.sesiones_memoria[clienteId].estado = estado;
          debug.sesiones_memoria[clienteId].isConnected = isConnected;
          debug.sesiones_memoria[clienteId].metodos_disponibles =
            Object.getOwnPropertyNames(Object.getPrototypeOf(session));
        } catch (err) {
          debug.sesiones_memoria[clienteId].error = err.message;
        }
      }
    }

    // Información de tenants en BD
    const result = await pool.query(
      "SELECT id, nombre, comercio FROM tenants ORDER BY id"
    );
    debug.tenants_bd = result.rows;

    // Diagnóstico detallado por cliente
    for (const cliente of result.rows) {
      const clienteId = String(cliente.id);
      const sessionDir =
        process.env.SESSION_FOLDER || path.join(__dirname, "tokens");

      const diag = {
        clienteId,
        nombre: cliente.nombre,
        comercio: cliente.comercio,
        sesion_en_memoria: !!sessions[clienteId],
        archivos_disco: {
          sessionDir: sessionDir,
          carpeta_existe: false,
          default_existe: false,
          singleton_lock_existe: false,
        },
        estado_final: "NO_INICIADA",
      };

      // Verificar archivos en disco (ruta local)
      const sessionPath = path.join(sessionDir, clienteId);
      if (fs.existsSync(sessionPath)) {
        diag.archivos_disco.carpeta_existe = true;
        const defaultPath = path.join(sessionPath, "Default");
        const singletonPath = path.join(sessionPath, "SingletonLock");
        diag.archivos_disco.default_existe = fs.existsSync(defaultPath);
        diag.archivos_disco.singleton_lock_existe =
          fs.existsSync(singletonPath);
      }

      // También verificar en /app/tokens
      const appTokensPath = `/app/tokens/${clienteId}`;
      if (fs.existsSync(appTokensPath)) {
        diag.archivos_disco.app_tokens = {
          carpeta_existe: true,
          default_existe: fs.existsSync(path.join(appTokensPath, "Default")),
          singleton_lock_existe: fs.existsSync(
            path.join(appTokensPath, "SingletonLock")
          ),
        };
      }

      // Determinar estado
      if (sessions[clienteId]) {
        try {
          diag.estado_final = await sessions[clienteId].getConnectionState();
          diag.is_connected = await sessions[clienteId].isConnected();
        } catch (err) {
          diag.estado_final = "ERROR";
          diag.error_estado = err.message;
        }
      } else if (
        diag.archivos_disco.default_existe ||
        (diag.archivos_disco.app_tokens &&
          diag.archivos_disco.app_tokens.default_existe)
      ) {
        diag.estado_final = "ARCHIVOS_DISPONIBLES";
      }

      debug.diagnostico_detallado.push(diag);
    }

    res.json(debug);
  } catch (error) {
    console.error("❌ Error en debug de estados:", error);
    res.status(500).json({ error: error.message, stack: error.stack });
  }
});

// **NUEVA FUNCIÓN: Crear carpetas automáticamente para clientes sin sesión**
async function crearCarpetasAutomaticamente() {
  console.log("🏗️ Verificando y creando carpetas para clientes sin sesión...");

  try {
    // Obtener clientes de la base de datos
    const result = await pool.query(
      "SELECT id, comercio FROM tenants ORDER BY id"
    );
    const clientesActivos = result.rows.map((row) => String(row.id));

    if (clientesActivos.length === 0) {
      console.log("⚠️ No hay clientes en la base de datos");
      return;
    }

    const sessionDir =
      process.env.SESSION_FOLDER || path.join(__dirname, "tokens");
    const productionPath = "/app/tokens";

    // Usar la ruta de producción si existe, sino la local
    const basePath = fs.existsSync(productionPath)
      ? productionPath
      : sessionDir;

    // Asegurar que la carpeta base existe
    if (!fs.existsSync(basePath)) {
      fs.mkdirSync(basePath, { recursive: true });
      console.log(`📁 Carpeta base creada: ${basePath}`);
    }

    let carpetasCreadas = 0;

    for (const clienteId of clientesActivos) {
      const carpetaCliente = path.join(basePath, clienteId);

      if (!fs.existsSync(carpetaCliente)) {
        try {
          // Crear carpeta del cliente
          fs.mkdirSync(carpetaCliente, { recursive: true });

          // Crear estructura básica de Chrome
          const defaultPath = path.join(carpetaCliente, "Default");
          fs.mkdirSync(defaultPath, { recursive: true });

          // Crear archivo básico de configuración para que Chrome reconozca la sesión
          const preferencesFile = path.join(defaultPath, "Preferences");
          const basicPreferences = {
            profile: {
              name: `WhatsApp-${clienteId}`,
              default_content_setting_values: {
                notifications: 1,
              },
            },
            whatsapp: {
              client_id: clienteId,
              created_at: new Date().toISOString(),
            },
          };

          fs.writeFileSync(
            preferencesFile,
            JSON.stringify(basicPreferences, null, 2)
          );

          console.log(
            `📁 Carpeta creada para cliente ${clienteId}: ${carpetaCliente}`
          );
          carpetasCreadas++;
        } catch (err) {
          console.error(
            `❌ Error creando carpeta para cliente ${clienteId}:`,
            err.message
          );
        }
      }
    }

    if (carpetasCreadas > 0) {
      console.log(`✅ Se crearon ${carpetasCreadas} carpetas nuevas`);
    } else {
      console.log("✅ Todas las carpetas ya existen");
    }
  } catch (err) {
    console.error("❌ Error creando carpetas automáticamente:", err);
  }
}

// Función para inicializar la aplicación
async function inicializarAplicacion() {
  try {
    console.log(
      `📁 Carpeta de sesiones configurada: ${
        process.env.SESSION_FOLDER || path.join(__dirname, "tokens")
      }`
    );
    console.log(
      `🔍 Verificando si existe /app/tokens:`,
      fs.existsSync("/app/tokens")
    );

    if (fs.existsSync("/app/tokens")) {
      const folders = fs
        .readdirSync("/app/tokens")
        .filter((item) => !isNaN(item));
      console.log(`📂 Carpetas numéricas encontradas en /app/tokens:`, folders);
    }

    // Esperar un poco para asegurar que la DB esté lista
    console.log("⏱️ Esperando conexión estable a la base de datos...");
    await new Promise((resolve) => setTimeout(resolve, 3000));

    await restaurarSesiones();
    await crearCarpetasAutomaticamente();

    console.log("🎉 Aplicación inicializada correctamente");
  } catch (error) {
    console.error("❌ Error durante la inicialización:", error);
  }
}

// Intentar iniciar el servidor with manejo de errores
const server = app
  .listen(PORT)
  .on("listening", async () => {
    console.log(`✅ Venom-service corriendo en puerto ${PORT}`);
    await inicializarAplicacion();
  })
  .on("error", (error) => {
    if (error.code === "EADDRINUSE") {
      console.error(
        `❌ Puerto ${PORT} ya está en uso. Intentando puerto alternativo...`
      );

      // Intentar con puerto aleatorio
      const server2 = app
        .listen(0)
        .on("listening", async () => {
          const actualPort = server2.address().port;
          console.log(
            `✅ Venom-service corriendo en puerto alternativo ${actualPort}`
          );
          await inicializarAplicacion();
        })
        .on("error", (err) => {
          console.error(`❌ Error fatal iniciando servidor:`, err);
          process.exit(1);
        });
    } else {
      console.error(`❌ Error iniciando servidor:`, error);
      process.exit(1);
    }
  });

// Nuevos endpoints para limpieza y reparación
app.post("/limpiar/:clienteId", async (req, res) => {
  const { clienteId } = req.params;
  const sessionDir =
    process.env.SESSION_FOLDER || path.join(__dirname, "tokens");

  try {
    console.log(`🧹 Iniciando limpieza completa para cliente ${clienteId}...`);

    // 1. Cerrar sesión activa si existe
    if (sessions[clienteId]) {
      try {
        await sessions[clienteId].close();
        console.log(`🔒 Sesión activa cerrada para cliente ${clienteId}`);
      } catch (err) {
        console.log(
          `⚠️ Error cerrando sesión activa para ${clienteId}:`,
          err.message
        );
      }
      delete sessions[clienteId];
    }

    // 2. Resetear contador de errores
    sessionErrors[clienteId] = 0;
    console.log(`🔄 Contador de errores reseteado para cliente ${clienteId}`);

    // 3. Eliminar carpeta de sesión completa
    const sessionPath = path.join(sessionDir, clienteId);
    if (fs.existsSync(sessionPath)) {
      fs.rmSync(sessionPath, { recursive: true, force: true });
      console.log(`🗑️ Carpeta de sesión eliminada: ${sessionPath}`);
    }

    // 4. Eliminar archivo QR HTML
    const qrPath = path.join(sessionDir, `${clienteId}.html`);
    if (fs.existsSync(qrPath)) {
      fs.unlinkSync(qrPath);
      console.log(`🗑️ Archivo QR eliminado: ${qrPath}`);
    }

    // 5. Limpiar QR en base de datos
    try {
      const result = await pool.query(
        "UPDATE tenants SET qr_code = NULL WHERE id = $1",
        [clienteId]
      );
      console.log(
        `🧹 QR limpiado en DB para cliente ${clienteId}, filas afectadas: ${result.rowCount}`
      );
    } catch (err) {
      console.error(
        `❌ Error limpiando QR en DB para cliente ${clienteId}:`,
        err
      );
    }

    // 6. Crear carpetas limpias
    await crearCarpetasAutomaticamente();

    console.log(`✅ Limpieza completa finalizada para cliente ${clienteId}`);

    res.json({
      success: true,
      message: `Cliente ${clienteId} limpiado completamente`,
      acciones: [
        "Sesión activa cerrada",
        "Contador de errores reseteado",
        "Carpeta de sesión eliminada",
        "Archivo QR eliminado",
        "QR limpiado en DB",
        "Carpetas base recreadas",
      ],
      siguiente_paso: `Usar /iniciar/${clienteId} para generar nuevo QR`,
    });
  } catch (err) {
    console.error(`❌ Error limpiando cliente ${clienteId}:`, err);
    res.status(500).json({
      success: false,
      error: "Error durante la limpieza",
      details: err.message,
    });
  }
});

app.post("/reparar-automatico/:clienteId", async (req, res) => {
  const { clienteId } = req.params;

  try {
    console.log(
      `🔧 Iniciando reparación automática para cliente ${clienteId}...`
    );

    // 1. Limpiar completamente
    await fetch(`http://localhost:${PORT}/limpiar/${clienteId}`, {
      method: "POST",
    });

    // 2. Esperar un poco
    await new Promise((resolve) => setTimeout(resolve, 2000));

    // 3. Iniciar nueva sesión
    await crearSesionConTimeout(clienteId, 60000, true);

    console.log(
      `✅ Reparación automática completada para cliente ${clienteId}`
    );

    res.json({
      success: true,
      message: `Cliente ${clienteId} reparado automáticamente`,
      qr_disponible_en: `/qr/${clienteId}`,
    });
  } catch (err) {
    console.error(
      `❌ Error en reparación automática para cliente ${clienteId}:`,
      err
    );
    res.status(500).json({
      success: false,
      error: "Error en reparación automática",
      details: err.message,
    });
  }
});

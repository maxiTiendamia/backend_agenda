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
  .then((client) => {
    console.log("✅ Conexión a PostgreSQL exitosa");
    client.release();
  })
  .catch((err) => {
    console.error("❌ Error al conectar con la base de datos:", err);
  });

function crearSesionConTimeout(clienteId, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("⏱ Tiempo de espera agotado para crear sesión")), timeoutMs);
    crearSesion(clienteId, false).then((res) => {
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
  if (sessions[sessionId]) {
    console.log(`🟡 Sesión ya activa para ${sessionId}`);
    return sessions[sessionId];
  }

  console.log(`⚙️ Iniciando sesión para ${sessionId}...`);
  const sessionDir = process.env.SESSION_FOLDER || path.join(__dirname, "sessions");
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
      browserArgs: ["--no-sandbox", "--disable-setuid-sandbox"],
      puppeteerOptions: { headless: "new" },
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

async function restaurarSesiones() {
  try {
    const result = await pool.query("SELECT id FROM tenants WHERE qr_code IS NOT NULL");
    for (const row of result.rows) {
      const clienteId = row.id;
      console.log(`🔄 Restaurando sesión previa para cliente ${clienteId}...`);
      await crearSesion(clienteId, false);
    }
  } catch (err) {
    console.error("❌ Error restaurando sesiones previas:", err);
  }
}

app.get("/iniciar/:clienteId", async (req, res) => {
  const { clienteId } = req.params;
  try {
    await crearSesionConTimeout(clienteId, 60000);
    res.send(`✅ Sesión iniciada para ${clienteId}. Escaneá el QR en /qr/${clienteId}`);
  } catch (error) {
    console.error("❌ Error al iniciar sesión:", error);
    res.status(500).send("Error al iniciar sesión");
  }
});

app.get("/qr/:clienteId", (req, res) => {
  const clienteId = req.params.clienteId;
  const filePath = path.join(__dirname, "sessions", `${clienteId}.html`);
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
    for (const cliente of result.rows) {
      const clienteId = String(cliente.id);
      if (sessions[clienteId]) {
        try {
          const estado = await sessions[clienteId].getConnectionState();
          estados.push({ clienteId, nombre: cliente.nombre, comercio: cliente.comercio, estado });
        } catch (err) {
          estados.push({ clienteId, nombre: cliente.nombre, comercio: cliente.comercio, estado: "ERROR" });
        }
      } else {
        estados.push({ clienteId, nombre: cliente.nombre, comercio: cliente.comercio, estado: "NO_INICIADA" });
      }
    }
    res.json(estados);
  } catch (error) {
    res.status(500).json({ error: "Error consultando clientes" });
  }
});

app.listen(PORT, async () => {
  console.log(`✅ Venom-service corriendo en puerto ${PORT}`);
  await restaurarSesiones();
});
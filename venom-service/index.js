const express = require("express");
const { Pool } = require("pg");
const fs = require("fs");
const path = require("path");
const venom = require("venom-bot");

const app = express();
const PORT = process.env.PORT || 3000;
app.use(express.json());

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: {
    rejectUnauthorized: false,
  },
});

const sessions = {};

pool
  .connect()
  .then((client) => {
    console.log("✅ Conexión a PostgreSQL exitosa");
    client.release();
  })
  .catch((err) => {
    console.error("❌ Error al conectar con la base de datos:", err);
  });

function crearSesionConTimeout(clienteId, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error("⏱ Tiempo de espera agotado para crear sesión"));
    }, timeoutMs);

    crearSesion(clienteId, false)
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
      puppeteerOptions: {
        headless: "new",
      },
      catchQR: async (base64Qr) => {
        if (!permitirGuardarQR) return;
        console.log("🟡 Generando QR para:", sessionId);

        const html = `
        <html>
          <body style="display:flex;justify-content:center;align-items:center;height:100vh;">
            <img src="${base64Qr}" />
          </body>
        </html>`;

        const qrPath = path.join(sessionDir, `${sessionId}.html`);
        fs.writeFileSync(qrPath, html);
        console.log(`✅ QR guardado en archivo: ${qrPath}`);

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

app.post("/crear_sesion", async (req, res) => {
  const { cliente_id } = req.body;

  if (!cliente_id) {
    return res.status(400).json({ error: "Falta cliente_id" });
  }

  try {
    await crearSesionConTimeout(cliente_id, 60000);
    res.status(200).json({ success: true });
  } catch (err) {
    console.error("❌ Error creando sesión desde POST:", err);
    res.status(500).json({ error: "Error generando QR" });
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

app.get("/qr_base64/:clienteId", async (req, res) => {
  const { clienteId } = req.params;

  try {
    const result = await pool.query("SELECT qr_code FROM tenants WHERE id = $1", [clienteId]);

    if (result.rows.length === 0 || !result.rows[0].qr_code) {
      return res.status(404).send("QR no encontrado para este cliente.");
    }

    res.send(result.rows[0].qr_code);
  } catch (err) {
    console.error("❌ Error al obtener QR desde la base:", err);
    res.status(500).send("Error al obtener el QR.");
  }
});

app.post("/enviar-mensaje", async (req, res) => {
  const { cliente_id, telefono, mensaje } = req.body;

  const session = sessions[String(cliente_id)];
  if (!session) {
    return res.status(404).json({ error: "Sesión no encontrada para este cliente" });
  }

  try {
    const state = await session.getConnectionState();
    if (state !== "CONNECTED") {
      return res.status(400).json({ error: `Sesión no conectada (estado: ${state})` });
    }

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
    const clientes = result.rows;

    for (const cliente of clientes) {
      const clienteId = String(cliente.id);

      if (sessions[clienteId]) {
        try {
          const estado = await sessions[clienteId].getConnectionState();
          estados.push({
            clienteId,
            nombre: cliente.nombre,
            comercio: cliente.comercio,
            estado,
          });
        } catch (err) {
          console.error(`❌ Error obteniendo estado de sesión ${clienteId}:`, err);
          estados.push({
            clienteId,
            nombre: cliente.nombre,
            comercio: cliente.comercio,
            estado: "ERROR",
          });
        }
      } else {
        estados.push({
          clienteId,
          nombre: cliente.nombre,
          comercio: cliente.comercio,
          estado: "NO_INICIADA",
        });
      }
    }

    res.json(estados);
  } catch (error) {
    console.error("❌ Error consultando clientes desde la DB:", error);
    res.status(500).json({ error: "Error consultando clientes" });
  }
});

app.post("/send", async (req, res) => {
  const { clienteId, to, message } = req.body;

  if (!clienteId || !to || !message) {
    return res.status(400).json({ error: "Faltan parámetros requeridos: clienteId, to, message" });
  }

  try {
    const client = await crearSesion(clienteId);
    await client.sendText(to, message);
    res.json({ status: "ok", to, message });
  } catch (error) {
    console.error("❌ Error al enviar mensaje:", error);
    res.status(500).json({ error: "Error al enviar mensaje" });
  }
});

app.listen(PORT, async () => {
  console.log(`✅ Venom-service corriendo en puerto ${PORT}`);
  await restaurarSesiones();
});
const { Pool } = require("pg");

const pool = new Pool({
  connectionString: process.env.DATABASE_URL || 'postgresql://reservas_user:reservas_pass@localhost:5432/reservas_db',
  ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: false } : false,
});

const express = require("express");
const fs = require("fs");
const path = require("path");
const venom = require("venom-bot");

const app = express();
const PORT = 3000;
app.use(express.json());

const sessions = {}; // Sesiones activas

// Función para crear una sesión por cliente
async function crearSesion(clienteId) {
  if (sessions[clienteId]) {
    console.log(`🟡 Sesión ya activa para ${clienteId}`);
    return sessions[clienteId];
  }

  console.log(`⚙️ Iniciando sesión para ${clienteId}...`);

  // Asegura que la carpeta 'sessions/' exista
  const sessionDir = path.join(__dirname, "sessions");
  if (!fs.existsSync(sessionDir)) {
    fs.mkdirSync(sessionDir);
    console.log("📁 Carpeta 'sessions' creada");
  }

  const client = await venom.create({
    session: clienteId,
    multidevice: true,
    disableWelcome: true,
    sessionFolder: sessionDir,
    browserArgs: ["--no-sandbox", "--disable-setuid-sandbox"],
    puppeteerOptions: {
      headless: "new", // ✅ compatible con Chrome 137+
    },
    catchQR: async (base64Qr) => {
  console.log("🟡 Generando QR para:", clienteId);
  const html = `
    <html>
      <body style="display:flex;justify-content:center;align-items:center;height:100vh;">
        <img src="${base64Qr}" />
      </body>
    </html>`;
  const qrPath = `./sessions/${clienteId}.html`;
  fs.writeFileSync(qrPath, html);
  console.log(`✅ QR guardado en: ${qrPath}`);

  // Guardar en base de datos
  try {
    await pool.query("UPDATE tenants SET qr_code = $1 WHERE client_id = $2", [base64Qr, clienteId]);
    console.log(`📬 QR guardado en DB para cliente ${clienteId}`);
  } catch (err) {
    console.error("❌ Error guardando QR en DB:", err);
  }
},
  });

  sessions[clienteId] = client;

  // Listener opcional para responder mensajes
  client.onMessage(async (message) => {
    if (message.body.toLowerCase() === "hola") {
      await client.sendText(message.from, "¡Hola! ¿En qué puedo ayudarte? 🤖");
    }
  });

  return client;
}

// 🔹 Iniciar sesión (genera QR)
app.get("/iniciar/:clienteId", async (req, res) => {
  const { clienteId } = req.params;

  try {
    await crearSesion(clienteId);
    res.send(
      `✅ Sesión iniciada para ${clienteId}. Escaneá el QR en /qr/${clienteId}`
    );
  } catch (error) {
    console.error("❌ Error al iniciar sesión:", error);
    res.status(500).send("Error al iniciar sesión");
  }
});

// 🔹 Mostrar QR
app.get("/qr/:clienteId", (req, res) => {
  const clienteId = req.params.clienteId;
  const filePath = path.join(__dirname, "sessions", `${clienteId}.html`);

  if (fs.existsSync(filePath)) {
    res.sendFile(filePath);
  } else {
    res
      .status(404)
      .send(`<h2>⚠️ Aún no se generó un QR para el cliente: ${clienteId}</h2>`);
  }
});

// 🔹 Enviar mensaje
app.post("/send", async (req, res) => {
  const { clienteId, to, message } = req.body;

  try {
    const client = await crearSesion(clienteId);
    await client.sendText(to, message);
    res.json({ status: "ok", to, message });
  } catch (error) {
    console.error("❌ Error al enviar mensaje:", error);
    res.status(500).json({ error: "Error al enviar mensaje" });
  }
});

app.listen(PORT, () => {
  console.log(`✅ Venom-service corriendo en puerto ${PORT}`);
});
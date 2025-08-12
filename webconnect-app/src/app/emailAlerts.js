const nodemailer = require('nodemailer');

// Configuración del transporter de email
const transporter = nodemailer.createTransport({
  service: 'gmail', // Puedes usar 'outlook', 'yahoo', etc.
  auth: {
    user: process.env.EMAIL_USER,
    pass: process.env.EMAIL_APP_PASSWORD // App Password de Gmail
  }
});

// Lista de destinatarios para alertas críticas
const ALERT_RECIPIENTS = [
  process.env.EMAIL_OWNER_1 || 'tu-email@gmail.com',
  process.env.EMAIL_OWNER_2 || 'socio-email@gmail.com'
];

/**
 * Envía alerta por email cuando un cliente pierde conexión
 */
async function sendConnectionLostAlert(sessionId, reason, attempts = 0, clientInfo = {}) {
  try {
    const timestamp = new Date().toLocaleString('es-AR', { 
      timeZone: 'America/Argentina/Buenos_Aires' 
    });
    
    // Obtener información del cliente desde BD
    let clienteNombre = 'Cliente Desconocido';
    
    try {
      const { pool } = require('./database');
      const result = await pool.query(
        'SELECT nombre FROM tenants WHERE id = $1', 
        [sessionId]
      );
      if (result.rows.length > 0) {
        clienteNombre = result.rows[0].nombre || `Cliente #${sessionId}`;
      }
    } catch (dbError) {
      console.error('[EMAIL-ALERT] Error obteniendo info del cliente:', dbError.message);
    }

    const severityLevel = attempts >= 3 ? '🔴 CRÍTICO' : attempts >= 2 ? '🟠 ALTO' : '🟡 MEDIO';
    const subject = `${severityLevel} - Cliente WhatsApp Desconectado: ${clienteNombre}`;

    const htmlBody = `
      <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 10px 10px 0 0;">
          <h1 style="margin: 0; font-size: 24px;">⚠️ Alerta de Conexión WhatsApp</h1>
          <p style="margin: 5px 0 0 0; opacity: 0.9;">${timestamp}</p>
        </div>
        
        <div style="background: #f8f9fa; padding: 20px; border-left: 4px solid #dc3545;">
          <h2 style="color: #dc3545; margin-top: 0;">Cliente Desconectado</h2>
          <table style="width: 100%; border-collapse: collapse;">
            <tr>
              <td style="padding: 8px 0; font-weight: bold; width: 150px;">Cliente:</td>
              <td style="padding: 8px 0;">${clienteNombre}</td>
            </tr>
            <tr>
              <td style="padding: 8px 0; font-weight: bold;">ID Sesión:</td>
              <td style="padding: 8px 0;">#${sessionId}</td>
            </tr>
            <tr>
              <td style="padding: 8px 0; font-weight: bold;">Motivo:</td>
              <td style="padding: 8px 0;">${reason}</td>
            </tr>
            <tr>
              <td style="padding: 8px 0; font-weight: bold;">Intentos Fallidos:</td>
              <td style="padding: 8px 0;">${attempts}/3</td>
            </tr>
            <tr>
              <td style="padding: 8px 0; font-weight: bold;">Severidad:</td>
              <td style="padding: 8px 0;">${severityLevel}</td>
            </tr>
          </table>
        </div>

        <div style="background: white; padding: 20px;">
          <h3 style="color: #333; margin-top: 0;">🔧 Acciones Recomendadas:</h3>
          <ul style="color: #555; line-height: 1.6;">
            <li><strong>Inmediata:</strong> Verificar estado del cliente en el panel de control</li>
            <li><strong>Si persiste:</strong> Regenerar QR manualmente usando /restart-qr/${sessionId}</li>
            <li><strong>Contactar cliente:</strong> Informar sobre la desconexión temporal</li>
            <li><strong>Verificar sistema:</strong> Revisar logs del servidor para problemas generales</li>
          </ul>
        </div>

        <div style="background: #e9ecef; padding: 15px; border-radius: 0 0 10px 10px; text-align: center;">
          <p style="margin: 0; color: #6c757d; font-size: 14px;">
            Sistema de Alertas - Backend Agenda WhatsApp<br>
            <strong>Servidor:</strong> ${process.env.NODE_ENV || 'development'} | 
            <strong>Timestamp:</strong> ${new Date().toISOString()}
          </p>
        </div>
      </div>
    `;

    const textBody = `
ALERTA DE CONEXIÓN WHATSAPP
${severityLevel}

Cliente: ${clienteNombre}
ID Sesión: #${sessionId}
Motivo: ${reason}
Intentos Fallidos: ${attempts}/3
Fecha/Hora: ${timestamp}

ACCIONES RECOMENDADAS:
1. Verificar estado del cliente en el panel
2. Si persiste: Regenerar QR con /restart-qr/${sessionId}
3. Contactar al cliente para informar la situación
4. Revisar logs del servidor

Sistema de Alertas - Backend Agenda WhatsApp
    `;

    const mailOptions = {
      from: `"Sistema WhatsApp" <${process.env.EMAIL_USER}>`,
      to: ALERT_RECIPIENTS.join(', '),
      subject: subject,
      text: textBody,
      html: htmlBody,
      priority: attempts >= 2 ? 'high' : 'normal'
    };

    const info = await transporter.sendMail(mailOptions);
    console.log(`[EMAIL-ALERT] ✅ Alerta enviada para cliente ${sessionId}:`, info.messageId);
    return true;

  } catch (error) {
    console.error('[EMAIL-ALERT] ❌ Error enviando alerta por email:', error.message);
    return false;
  }
}

/**
 * Envía alerta de reconexión exitosa
 */
async function sendReconnectionSuccessAlert(sessionId, previousFailures = 0) {
  try {
    // Solo enviar si había fallos previos
    if (previousFailures === 0) return;

    const timestamp = new Date().toLocaleString('es-AR', { 
      timeZone: 'America/Argentina/Buenos_Aires' 
    });
    
    // Obtener información del cliente
    let clienteNombre = 'Cliente Desconocido';
    try {
      const { pool } = require('./database');
      const result = await pool.query(
        'SELECT nombre FROM tenants WHERE id = $1', 
        [sessionId]
      );
      if (result.rows.length > 0) {
        clienteNombre = result.rows[0].nombre || `Cliente #${sessionId}`;
      }
    } catch (dbError) {
      console.error('[EMAIL-ALERT] Error obteniendo info del cliente:', dbError.message);
    }

    const subject = `✅ RESUELTO - Cliente WhatsApp Reconectado: ${clienteNombre}`;

    const htmlBody = `
      <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; padding: 20px; border-radius: 10px 10px 0 0;">
          <h1 style="margin: 0; font-size: 24px;">✅ Conexión Restaurada</h1>
          <p style="margin: 5px 0 0 0; opacity: 0.9;">${timestamp}</p>
        </div>
        
        <div style="background: #d4edda; padding: 20px; border-left: 4px solid #28a745;">
          <h2 style="color: #155724; margin-top: 0;">🎉 Cliente Reconectado Exitosamente</h2>
          <p><strong>Cliente:</strong> ${clienteNombre}</p>
          <p><strong>ID Sesión:</strong> #${sessionId}</p>
          <p><strong>Fallos Previos:</strong> ${previousFailures}</p>
          <p><strong>Estado:</strong> ✅ Completamente Operativo</p>
        </div>

        <div style="background: white; padding: 20px; border-radius: 0 0 10px 10px; text-align: center;">
          <p style="margin: 0; color: #6c757d; font-size: 14px;">
            El cliente puede volver a recibir y enviar mensajes normalmente.
          </p>
        </div>
      </div>
    `;

    const mailOptions = {
      from: `"Sistema WhatsApp" <${process.env.EMAIL_USER}>`,
      to: ALERT_RECIPIENTS.join(', '),
      subject: subject,
      html: htmlBody,
      priority: 'low'
    };

    const info = await transporter.sendMail(mailOptions);
    console.log(`[EMAIL-ALERT] ✅ Alerta de reconexión enviada para cliente ${sessionId}:`, info.messageId);
    return true;

  } catch (error) {
    console.error('[EMAIL-ALERT] ❌ Error enviando alerta de reconexión:', error.message);
    return false;
  }
}

/**
 * Envía resumen diario de estado de sesiones
 */
async function sendDailySummary() {
  try {
    const { getAllSessionsStatus } = require('./wppconnect');
    const sessionStatus = await getAllSessionsStatus();
    
    const total = Object.keys(sessionStatus).length;
    const connected = Object.values(sessionStatus).filter(s => s.connected).length;
    const disconnected = total - connected;
    
    if (total === 0) return; // No enviar si no hay sesiones

    const timestamp = new Date().toLocaleString('es-AR', { 
      timeZone: 'America/Argentina/Buenos_Aires' 
    });

    const subject = `📊 Resumen Diario WhatsApp - ${connected}/${total} Conectados`;

    // Generar tabla de sesiones
    let sessionTable = '';
    for (const [sessionId, status] of Object.entries(sessionStatus)) {
      const icon = status.connected ? '✅' : '❌';
      const state = status.connectionState || 'DESCONOCIDO';
      sessionTable += `
        <tr>
          <td style="padding: 8px; border: 1px solid #ddd;">${icon} ${sessionId}</td>
          <td style="padding: 8px; border: 1px solid #ddd;">${status.connected ? 'Conectado' : 'Desconectado'}</td>
          <td style="padding: 8px; border: 1px solid #ddd;">${state}</td>
        </tr>
      `;
    }

    const htmlBody = `
      <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #007bff 0%, #6f42c1 100%); color: white; padding: 20px; border-radius: 10px 10px 0 0;">
          <h1 style="margin: 0; font-size: 24px;">📊 Resumen Diario</h1>
          <p style="margin: 5px 0 0 0; opacity: 0.9;">${timestamp}</p>
        </div>
        
        <div style="background: white; padding: 20px;">
          <div style="display: flex; justify-content: space-around; margin-bottom: 20px;">
            <div style="text-align: center; padding: 15px; background: #e8f5e8; border-radius: 8px;">
              <h3 style="margin: 0; color: #28a745; font-size: 28px;">${connected}</h3>
              <p style="margin: 5px 0 0 0; color: #666;">Conectados</p>
            </div>
            <div style="text-align: center; padding: 15px; background: #f8e8e8; border-radius: 8px;">
              <h3 style="margin: 0; color: #dc3545; font-size: 28px;">${disconnected}</h3>
              <p style="margin: 5px 0 0 0; color: #666;">Desconectados</p>
            </div>
          </div>

          <h3>Estado Detallado de Sesiones:</h3>
          <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
            <thead>
              <tr style="background: #f8f9fa;">
                <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Cliente</th>
                <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Estado</th>
                <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Conexión</th>
              </tr>
            </thead>
            <tbody>
              ${sessionTable}
            </tbody>
          </table>
        </div>

        <div style="background: #f8f9fa; padding: 15px; border-radius: 0 0 10px 10px; text-align: center;">
          <p style="margin: 0; color: #6c757d; font-size: 14px;">
            Resumen automático generado por Sistema WhatsApp Backend
          </p>
        </div>
      </div>
    `;

    const mailOptions = {
      from: `"Sistema WhatsApp" <${process.env.EMAIL_USER}>`,
      to: ALERT_RECIPIENTS.join(', '),
      subject: subject,
      html: htmlBody,
      priority: 'low'
    };

    const info = await transporter.sendMail(mailOptions);
    console.log(`[EMAIL-ALERT] ✅ Resumen diario enviado:`, info.messageId);
    return true;

  } catch (error) {
    console.error('[EMAIL-ALERT] ❌ Error enviando resumen diario:', error.message);
    return false;
  }
}

module.exports = {
  sendConnectionLostAlert,
  sendReconnectionSuccessAlert,
  sendDailySummary
};
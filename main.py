from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
import datetime
import requests
from dotenv import load_dotenv
load_dotenv() 
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_basicauth import BasicAuth
from models import db  # Importa db desde models.py
from flask_migrate import Migrate


# Crear la aplicación Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'SECRET_KEY')

# Configuración de la base de datos
db_url = os.getenv('DATABASE_URL', 'postgresql://reservas_user:reservas_pass@localhost:5432/reservas_db')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
migrate = Migrate(app, db)

# Configuración de Basic Auth para el panel de admin
app.config['BASIC_AUTH_USERNAME'] = os.getenv('ADMIN_USER', 'admin')
app.config['BASIC_AUTH_PASSWORD'] = os.getenv('ADMIN_PASSWORD', 'BackAgenda2025')
basic_auth = BasicAuth(app)


from admin import init_admin
init_admin(app, db)


from models import db  

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    # En desarrollo puedes usar debug=True
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=True)


# Cargar variables de entorno
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

# Ruta del archivo secreto en Render
#SERVICE_ACCOUNT_FILE = "/etc/secrets/calendario-zichi-d98b415d5008.json"

# Crear credenciales de Google
#credentials = service_account.Credentials.from_service_account_file(
 #   SERVICE_ACCOUNT_FILE,
   # scopes=["https://www.googleapis.com/auth/calendar"]
#)

# Inicializar cliente de Google Calendar
#calendar_service = build("calendar", "v3", credentials=credentials)


# 🔍 Obtener turnos disponibles
@app.route("/reservar", methods=["POST"])
def reservar_turno():
    data = request.json
    nombre = data.get("nombre")
    telefono = data.get("telefono")
    inicio = data.get("inicio")  # Formato ISO 8601
    fin = data.get("fin")        # Formato ISO 8601

    from models import Tenant, TenantCredentials, TenantConfig
    tenant = Tenant.query.filter_by(telefono=telefono).first()
    if not tenant:
        return jsonify({"error": "Cliente no encontrado"}), 404

    creds = TenantCredentials.query.filter_by(tenant_id=tenant.id).first()
    config = TenantConfig.query.filter_by(tenant_id=tenant.id).first()
    if not creds or not config or not config.calendar_id:
        return jsonify({"error": "Faltan credenciales o calendar_id"}), 400

    import json
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    service_account_info = json.loads(creds.google_service_account_info)
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )

    calendar_service = build("calendar", "v3", credentials=credentials)
    calendar_id = config.calendar_id  # Usar el calendar_id guardado

    evento = {
        "summary": f"Reserva - {nombre}",
        "description": f"Reserva realizada por {nombre}, Tel: {telefono}",
        "start": {"dateTime": inicio, "timeZone": "America/Montevideo"},
        "end": {"dateTime": fin, "timeZone": "America/Montevideo"}
    }

    created_event = calendar_service.events().insert(
        calendarId=calendar_id,
        body=evento
    ).execute()

    mensaje = f"Hola {nombre}, tu turno fue reservado con éxito para el {inicio}."
    enviar_whatsapp(telefono, mensaje)

    return jsonify({"status": "ok", "evento_id": created_event["id"]})

    # Mandar mensaje de confirmación por WhatsApp
    mensaje = f"Hola {nombre}, tu turno fue reservado con éxito para el {inicio}."
    enviar_whatsapp(telefono, mensaje)

    return jsonify({"status": "ok", "evento_id": created_event["id"]})


# 📲 Enviar WhatsApp con Twilio
def enviar_whatsapp(numero_cliente, mensaje):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    data = {
        "From": f"whatsapp:{TWILIO_WHATSAPP_NUMBER}",
        "To": f"whatsapp:{numero_cliente}",
        "Body": mensaje
    }
    auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    response = requests.post(url, data=data, auth=auth)
    return response.status_code


# 🏠 Ruta básica
@app.route("/", methods=["GET"])
def home():
    return "API de reservas online funcionando."

if __name__ == "__main__":
    app.run(debug=True)

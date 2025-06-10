from datetime import datetime

from fastapi import FastAPI, Request, Query

from app.calendar import get_available_slots, create_event
from app.config import VERIFY_TOKEN, CALENDAR_ID
from app.whatsapp import send_whatsapp_message

app = FastAPI()

# Guardar selección temporal (esto puede ir en base de datos en producción)
user_selection = {}
user_greeted = set()

from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import datetime
import requests
from dotenv import load_dotenv
load_dotenv()
from flask import Flask
from flask_basicauth import BasicAuth
from app.models import db  # Importa db desde models.py
from flask_migrate import Migrate
from google.oauth2 import service_account
from googleapiclient.discovery import build
from app.models import Tenant, TenantCredentials, TenantConfig
import os
import json
from google.oauth2 import service_account




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


from app.admin import init_admin
init_admin(app, db)


from app.models import db

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    # En desarrollo puedes usar debug=True
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=True)


def get_calendar_service_for_tenant(telefono):
    tenant = Tenant.query.filter_by(telefono=telefono).first()
    if not tenant:
        raise Exception("Client not found")

    creds = TenantCredentials.query.filter_by(tenant_id=tenant.id).first()
    config = TenantConfig.query.filter_by(tenant_id=tenant.id).first()
    if not creds or not config or not config.calendar_id:
        raise Exception("Missing credentials or calendar_id")

    service_account_info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    calendar_service = build("calendar", "v3", credentials=credentials)
    calendar_id = config.calendar_id
    return calendar_service, calendar_id

# Inicializar cliente de Google Calendar
calendar_service = build("calendar", "v3", credentials=credentials)


# 🔍 Obtener turnos disponibles
@app.route("/reservar", methods=["POST"])
def reservar_turno():
    data = request.json
    nombre = data.get("nombre")
    telefono = data.get("telefono")
    inicio = data.get("inicio")  # Formato ISO 8601
    fin = data.get("fin")        # Formato ISO 8601

    from app.models import Tenant, TenantCredentials, TenantConfig
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


@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/webhook")
def verify_token(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge")
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return int(hub_challenge)
    return {"error": "Invalid token"}, 403

@app.post("/webhook")
async def receive_message(request: Request):
    data = await request.json()
    try:
        changes = data.get('entry', [])[0].get('changes', [])[0].get('value', {})
        messages = changes.get('messages')

        if not messages:
            return {"status": "ignored"}  # No hay mensaje para procesar

        entry = messages[0]
        user_msg = entry['text']['body']
        from_number = entry['from']

        if from_number not in user_greeted:
            bienvenida = (
                "Hola 👋 Bienvenido/a a nuestra agenda automatizada.\n"
                "Respondé con el número correspondiente:\n"
                "1️⃣ para reservar un turno\n"
                "2️⃣ para que te contactemos personalmente."
            )
            await send_whatsapp_message(from_number, bienvenida)
            user_greeted.add(from_number)
            return {"status": "greeted"}

        if from_number in user_selection and user_msg.isdigit():
            index = int(user_msg) - 1
            slots = user_selection[from_number]
            if 0 <= index < len(slots):
                selected_slot = slots[index]
                create_event(CALENDAR_ID, selected_slot, from_number)
                await send_whatsapp_message(from_number, f"✅ Turno reservado para: {selected_slot}")
                del user_selection[from_number]
            else:
                await send_whatsapp_message(from_number, "Número inválido. Por favor, elige una opción válida.")
            return {"status": "handled"}

        if user_msg == "1" or "turno" in user_msg.lower():
            slots = get_available_slots(CALENDAR_ID)
            # Filtrar duplicados por fecha y hora
            unique_slots = []
            seen = set()
            for slot in slots:
                key = datetime.strptime(slot, "%d/%m %H:%M")
                if key not in seen:
                    seen.add(key)
                    unique_slots.append(slot)

            user_selection[from_number] = unique_slots

            if unique_slots:
                msg = "Estos son los próximos turnos disponibles:\n"
                for idx, slot in enumerate(unique_slots):
                    msg += f"{idx+1}. {slot}\n"
                msg += "\nRespondé con el número del turno que querés reservar."
            else:
                msg = "No hay turnos disponibles por el momento."
            await send_whatsapp_message(from_number, msg)
        elif user_msg == "2" or "contacto" in user_msg.lower():
            await send_whatsapp_message(from_number, "Perfecto, en breve nos pondremos en contacto contigo personalmente. 🙌")
        else:
            await send_whatsapp_message(from_number, "¿Querés reservar un turno? Respondé con '1'. Si preferís que te contactemos, respondé con '2'.")

    except Exception as e:
        print("Error al procesar:", e)
    return {"status": "received"}

from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
import datetime
import requests

app = Flask(__name__)

# Cargar variables de entorno
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

# Ruta del archivo secreto en Render
SERVICE_ACCOUNT_FILE = "/etc/secrets/calendario-zichi-d98b415d5008.json"

# Crear credenciales de Google
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/calendar"]
)

# Inicializar cliente de Google Calendar
calendar_service = build("calendar", "v3", credentials=credentials)


# 🔍 Obtener turnos disponibles
@app.route("/disponibles", methods=["GET"])
def obtener_turnos():
    now = datetime.datetime.utcnow().isoformat() + "Z"
    end = (datetime.datetime.utcnow() + datetime.timedelta(days=7)).isoformat() + "Z"
    events_result = calendar_service.events().list(
        calendarId=GOOGLE_CALENDAR_ID,
        timeMin=now,
        timeMax=end,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = events_result.get("items", [])

    disponibles = []
    for e in events:
        disponibles.append({
            "inicio": e["start"].get("dateTime"),
            "fin": e["end"].get("dateTime")
        })

    return jsonify(disponibles)


# 📆 Reservar turno
@app.route("/reservar", methods=["POST"])
def reservar_turno():
    data = request.json
    nombre = data.get("nombre")
    telefono = data.get("telefono")
    inicio = data.get("inicio")  # Formato ISO 8601
    fin = data.get("fin")        # Formato ISO 8601

    evento = {
        "summary": f"Reserva - {nombre}",
        "description": f"Reserva realizada por {nombre}, Tel: {telefono}",
        "start": {"dateTime": inicio, "timeZone": "America/Montevideo"},
        "end": {"dateTime": fin, "timeZone": "America/Montevideo"}
    }

    created_event = calendar_service.events().insert(
        calendarId=GOOGLE_CALENDAR_ID,
        body=evento
    ).execute()

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

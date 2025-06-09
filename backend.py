from flask import Flask, request, jsonify
import os
import requests
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# === CONFIGURACIÓN ===
WHATSAPP_API_URL = "https://api.twilio.com/2010-04-01/Accounts/{AccountSID}/Messages.json"
ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
FROM_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

# === GOOGLE CALENDAR ===
SCOPES = ["https://www.googleapis.com/auth/calendar"]
SERVICE_ACCOUNT_FILE = "credentials.json"  # Debes subir esto a tu servidor de Render
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
calendar_service = build("calendar", "v3", credentials=credentials)

# === FUNCIONES ===
def get_available_slots():
    now = datetime.utcnow()
    end = now + timedelta(days=1)
    events_result = calendar_service.freebusy().query(
        body={
            "timeMin": now.isoformat() + 'Z',
            "timeMax": end.isoformat() + 'Z',
            "timeZone": "UTC",
            "items": [{"id": CALENDAR_ID}]
        }
    ).execute()

    busy_times = events_result['calendars'][CALENDAR_ID]['busy']
    available = []
    slot = now.replace(minute=0, second=0, microsecond=0)
    while slot < end:
        if not any(b['start'] <= slot.isoformat() + 'Z' < b['end'] for b in busy_times):
            available.append(slot.strftime("%Y-%m-%d %H:%M"))
        slot += timedelta(minutes=30)
    return available


def create_calendar_event(start_time):
    end_time = (datetime.strptime(start_time, "%Y-%m-%d %H:%M") + timedelta(minutes=30)).isoformat()
    event = {
        'summary': 'Reserva vía WhatsApp',
        'start': {'dateTime': start_time + ":00", 'timeZone': 'UTC'},
        'end': {'dateTime': end_time + ":00", 'timeZone': 'UTC'}
    }
    calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()


def send_whatsapp_message(to, body):
    payload = {
        'From': f"whatsapp:{FROM_NUMBER}",
        'To': f"whatsapp:{to}",
        'Body': body
    }
    requests.post(WHATSAPP_API_URL.format(AccountSID=ACCOUNT_SID), data=payload, auth=(ACCOUNT_SID, AUTH_TOKEN))

# === RUTA PRINCIPAL ===
@app.route("/webhook", methods=["POST"])
def whatsapp_webhook():
    data = request.form
    msg = data.get("Body", "").strip().lower()
    sender = data.get("From")

    if "turno" in msg or "reserva" in msg:
        slots = get_available_slots()
        if not slots:
            send_whatsapp_message(sender, "No hay turnos disponibles hoy. Intenta mañana.")
        else:
            options = "\n".join(f"{i+1}. {slot}" for i, slot in enumerate(slots[:5]))
            send_whatsapp_message(sender, f"Turnos disponibles hoy:\n{options}\nResponde con el número del turno que quieres reservar.")
            # Aquí podrías guardar estado temporal del usuario para saber qué turno eligió
    elif msg.isdigit():
        idx = int(msg) - 1
        slots = get_available_slots()
        if 0 <= idx < len(slots[:5]):
            create_calendar_event(slots[idx])
            send_whatsapp_message(sender, f"Tu turno ha sido reservado para {slots[idx]} UTC. ¡Gracias!")
        else:
            send_whatsapp_message(sender, "Opción inválida. Intenta de nuevo.")
    else:
        send_whatsapp_message(sender, "Hola 👋 Soy tu asistente de reservas. Escribe 'turno' o 'reserva' para ver los horarios disponibles.")

    return jsonify({"status": "ok"})

# === PING ===
@app.route("/")
def index():
    return "Bot de WhatsApp para reservas conectado a Google Calendar."

# === INICIO ===
if __name__ == "__main__":
    app.run(debug=True)

import os
from datetime import datetime, timedelta
import pytz
from google.oauth2 import service_account
from googleapiclient.discovery import build
from config import CALENDAR_ID, TURN_DURATION_MINUTES, WORKING_HOURS, TIMEZONE

# Autenticación con Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
SERVICE_ACCOUNT_FILE = 'credentials.json'

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
service = build('calendar', 'v3', credentials=credentials)

# Guarda temporalmente los turnos disponibles (sin persistencia por ahora)
TURNOS_TEMP = []

def obtener_horarios_disponibles():
    global TURNOS_TEMP
    ahora = datetime.now(pytz.timezone(TIMEZONE))
    inicio = ahora.replace(hour=int(WORKING_HOURS['start'].split(':')[0]),
                           minute=0, second=0, microsecond=0)
    fin = ahora.replace(hour=int(WORKING_HOURS['end'].split(':')[0]),
                        minute=0, second=0, microsecond=0)

    eventos = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=inicio.isoformat(),
        timeMax=fin.isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute().get('items', [])

    ocupados = [(e['start']['dateTime'], e['end']['dateTime']) for e in eventos if 'dateTime' in e['start']]
    turnos = []
    actual = inicio
    while actual + timedelta(minutes=TURN_DURATION_MINUTES) <= fin:
        ocupado = any(datetime.fromisoformat(o[0]) <= actual < datetime.fromisoformat(o[1]) for o in ocupados)
        if not ocupado:
            turnos.append({
                'hora': actual.strftime('%H:%M'),
                'fecha': actual.strftime('%d/%m/%Y'),
                'datetime': actual
            })
        actual += timedelta(minutes=TURN_DURATION_MINUTES)

    TURNOS_TEMP = turnos
    return turnos

def reservar_turno(indice):
    global TURNOS_TEMP
    try:
        seleccionado = TURNOS_TEMP[indice]
        evento = {
            'summary': 'Reserva desde WhatsApp',
            'start': {
                'dateTime': seleccionado['datetime'].isoformat(),
                'timeZone': TIMEZONE
            },
            'end': {
                'dateTime': (seleccionado['datetime'] + timedelta(minutes=TURN_DURATION_MINUTES)).isoformat(),
                'timeZone': TIMEZONE
            }
        }
        service.events().insert(calendarId=CALENDAR_ID, body=evento).execute()
        return seleccionado
    except Exception as e:
        print(f"Error al reservar: {e}")
        return None
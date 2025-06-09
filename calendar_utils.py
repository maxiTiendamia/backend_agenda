import datetime
import pytz
from google.oauth2 import service_account
from googleapiclient.discovery import build
from config import CALENDAR_ID, TIMEZONE, TURN_DURATION_MINUTES, WORKING_HOURS

# Ruta al archivo de credenciales de Google
CREDENTIALS_PATH = "/etc/secrets/calendario-zichi-d98b415d5008.json"
credentials = service_account.Credentials.from_service_account_file(CREDENTIALS_PATH)
service = build("calendar", "v3", credentials=credentials)

def obtener_horarios_disponibles():
    ahora = datetime.datetime.now(pytz.timezone(TIMEZONE))
    hoy_inicio = ahora.replace(hour=int(WORKING_HOURS['start'].split(':')[0]), minute=0, second=0, microsecond=0)
    hoy_fin = ahora.replace(hour=int(WORKING_HOURS['end'].split(':')[0]), minute=0, second=0, microsecond=0)

    eventos = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=hoy_inicio.isoformat(),
        timeMax=hoy_fin.isoformat(),
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    ocupados = [
        (
            datetime.datetime.fromisoformat(e['start']['dateTime']).astimezone(pytz.timezone(TIMEZONE)),
            datetime.datetime.fromisoformat(e['end']['dateTime']).astimezone(pytz.timezone(TIMEZONE))
        )
        for e in eventos.get("items", [])
    ]

    disponibles = []
    cursor = hoy_inicio
    delta = datetime.timedelta(minutes=TURN_DURATION_MINUTES)

    while cursor + delta <= hoy_fin:
        solapa = any(start < cursor + delta and cursor < end for start, end in ocupados)
        if not solapa:
            disponibles.append(cursor)
        cursor += delta

    return disponibles

def reservar_turno(indice):
    disponibles = obtener_horarios_disponibles()
    if indice < 0 or indice >= len(disponibles):
        return None

    inicio = disponibles[indice]
    fin = inicio + datetime.timedelta(minutes=TURN_DURATION_MINUTES)

    evento = {
        "summary": "Turno reservado por WhatsApp",
        "start": {
            "dateTime": inicio.isoformat(),
            "timeZone": TIMEZONE
        },
        "end": {
            "dateTime": fin.isoformat(),
            "timeZone": TIMEZONE
        }
    }

    creado = service.events().insert(calendarId=CALENDAR_ID, body=evento).execute()
    return creado.get("htmlLink")
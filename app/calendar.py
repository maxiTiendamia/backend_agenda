import datetime
import json
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar']

credentials_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
if not credentials_json:
    raise RuntimeError("GOOGLE_CREDENTIALS_JSON environment variable is not set.")

credentials_info = json.loads(credentials_json)
credentials = service_account.Credentials.from_service_account_info(
    credentials_info, scopes=SCOPES)

service = build('calendar', 'v3', credentials=credentials)

def get_available_slots(calendar_id):
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    events_result = service.events().list(
        calendarId=calendar_id, timeMin=now,
        maxResults=5, singleEvents=True,
        orderBy='startTime').execute()
    events = events_result.get('items', [])

    slots = []
    for e in events:
        start = e['start'].get('dateTime') or e['start'].get('date')
        if start:
            dt = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
            slots.append(dt.strftime('%d/%m %H:%M'))
    return slots

def create_event(calendar_id, slot_str, user_phone, summary="Turno reservado", description="Reservado automáticamente por WhatsApp Bot"):
    dt = datetime.datetime.strptime(slot_str, '%d/%m %H:%M')
    start_time = dt.isoformat()
    end_time = (dt + datetime.timedelta(minutes=30)).isoformat()

    event = {
        'summary': summary,
        'description': f'{description} para {user_phone}',
        'start': {
            'dateTime': start_time,
            'timeZone': 'America/Montevideo',
        },
        'end': {
            'dateTime': end_time,
            'timeZone': 'America/Montevideo',
        },
    }
    event = service.events().insert(calendarId=calendar_id, body=event).execute()
    return event.get('id')
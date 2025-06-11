import datetime
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar']

def build_service(service_account_info):
    creds = service_account.Credentials.from_service_account_info(
        json.loads(service_account_info),
        scopes=SCOPES
    )
    return build('calendar', 'v3', credentials=creds)

def get_available_slots(calendar_id, service_account_info):
    service = build_service(service_account_info)
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

def create_event(calendar_id, slot_str, user_phone, service_account_info, summary="Turno reservado", description="Reservado automáticamente por WhatsApp Bot"):
    service = build_service(service_account_info)
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
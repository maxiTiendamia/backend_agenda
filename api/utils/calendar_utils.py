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


def get_available_slots(calendar_id, credentials_json, working_hours_json, duration_minutes=30):
    service = build_service(credentials_json)
    now = datetime.datetime.utcnow()
    start_check = now.replace(minute=0, second=0, microsecond=0)
    end_check = start_check + datetime.timedelta(days=10)

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=start_check.isoformat() + 'Z',
        timeMax=end_check.isoformat() + 'Z',
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    busy = [(datetime.datetime.fromisoformat(e['start'].get('dateTime', e['start'].get('date'))),
             datetime.datetime.fromisoformat(e['end'].get('dateTime', e['end'].get('date')))) for e in events_result.get('items', [])]

    available_slots = []
    working_hours = json.loads(working_hours_json)
    current = start_check

    while current < end_check:
        day_name = current.strftime('%A').lower()
        if day_name in working_hours:
            for block in working_hours[day_name]:
                start_str, end_str = block.split('-')
                block_start = current.replace(hour=int(start_str.split(':')[0]), minute=int(start_str.split(':')[1]))
                block_end = current.replace(hour=int(end_str.split(':')[0]), minute=int(end_str.split(':')[1]))

                slot = block_start
                while slot + datetime.timedelta(minutes=duration_minutes) <= block_end:
                    slot_end = slot + datetime.timedelta(minutes=duration_minutes)
                    overlapping = any(bs < slot_end and be > slot for bs, be in busy)
                    if not overlapping and slot > now:
                        available_slots.append(slot.strftime('%d/%m %H:%M'))
                        if len(available_slots) >= 15:
                            return available_slots
                    slot += datetime.timedelta(minutes=duration_minutes)
        current += datetime.timedelta(days=1)

    return available_slots


def create_event(calendar_id, slot_str, user_phone, service_account_info, duration_minutes=30, summary="Turno reservado", description="Reservado automáticamente por WhatsApp Bot"):
    service = build_service(service_account_info)
    dt = datetime.datetime.strptime(slot_str, '%d/%m %H:%M')
    start_time = dt.isoformat()
    end_time = (dt + datetime.timedelta(minutes=duration_minutes)).isoformat()

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
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

def get_available_slots(calendar_id, credentials_json, working_hours_json, duration_minutes=40):
    service = build_service(credentials_json)
    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    now_str = now.isoformat()

    # Obtener todos los eventos futuros del calendario
    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=now_str,
        maxResults=2500,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    busy_times = []
    for e in events_result.get('items', []):
        start = e['start'].get('dateTime')
        end = e['end'].get('dateTime')
        if start and end:
            start_dt = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
            end_dt = datetime.datetime.fromisoformat(end.replace('Z', '+00:00'))
            busy_times.append((start_dt, end_dt))

    # Parsear horarios de trabajo
    working_hours = json.loads(working_hours_json or '{}')
    available_slots = []
    days_checked = 0
    max_slots = 15

    current_day = now
    while len(available_slots) < max_slots and days_checked < 30:
        weekday = current_day.strftime('%A').lower()
        if weekday in working_hours:
            for period in working_hours[weekday]:
                try:
                    start_str, end_str = period.split("-")
                    start_hour = datetime.datetime.strptime(start_str.strip(), "%H:%M").time()
                    end_hour = datetime.datetime.strptime(end_str.strip(), "%H:%M").time()
                except Exception:
                    continue

                start_dt = datetime.datetime.combine(current_day.date(), start_hour, tzinfo=datetime.timezone.utc)
                end_dt = datetime.datetime.combine(current_day.date(), end_hour, tzinfo=datetime.timezone.utc)

                slot_time = start_dt
                while slot_time + datetime.timedelta(minutes=duration_minutes) <= end_dt:
                    slot_end = slot_time + datetime.timedelta(minutes=duration_minutes)
                    # Verificar superposición con eventos existentes
                    overlap = any(bs <= slot_time < be or bs < slot_end <= be for bs, be in busy_times)
                    if not overlap and slot_time > now:
                        available_slots.append(slot_time.strftime('%d/%m %H:%M'))
                        if len(available_slots) >= max_slots:
                            break
                    slot_time += datetime.timedelta(minutes=duration_minutes)

        current_day += datetime.timedelta(days=1)
        days_checked += 1

    return available_slots

def create_event(calendar_id, slot_str, user_phone, service_account_info, summary="Turno reservado", description="Reservado automáticamente por WhatsApp Bot"):
    service = build_service(service_account_info)
    dt = datetime.datetime.strptime(slot_str, '%d/%m %H:%M')
    dt = dt.replace(tzinfo=datetime.timezone.utc)
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

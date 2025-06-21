import datetime
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar']
URUGUAY_TZ = datetime.timezone(datetime.timedelta(hours=-3))

def build_service(service_account_info):
    creds = service_account.Credentials.from_service_account_info(
        json.loads(service_account_info),
        scopes=SCOPES
    )
    return build('calendar', 'v3', credentials=creds)

def get_available_slots(calendar_id, credentials_json, working_hours_json, duration_minutes=30, max_days=7):
    service = build_service(credentials_json)
    now = datetime.datetime.now(tz=URUGUAY_TZ)
    end_date = now + datetime.timedelta(days=max_days)

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=now.astimezone(datetime.timezone.utc).isoformat(),
        timeMax=end_date.astimezone(datetime.timezone.utc).isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])
    busy = []
    for e in events:
        start = e['start'].get('dateTime') or e['start'].get('date')
        end = e['end'].get('dateTime') or e['end'].get('date')
        if start and end:
            start_dt = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
            end_dt = datetime.datetime.fromisoformat(end.replace('Z', '+00:00'))
            busy.append((start_dt, end_dt))

    if isinstance(working_hours_json, str):
        try:
            working_hours = json.loads(working_hours_json)
        except json.JSONDecodeError:
            return []
    else:
        working_hours = working_hours_json

    if isinstance(working_hours, list):
        normalized = {}
        for item in working_hours:
            if isinstance(item, dict) and 'day' in item and 'from' in item and 'to' in item:
                day = item['day'].lower()
                normalized.setdefault(day, []).append({"from": item['from'], "to": item['to']})
        working_hours = normalized

    available = []
    current = now

    while current < end_date:
        day_str = current.strftime('%A').lower()
        if day_str in working_hours:
            for period in working_hours[day_str]:
                if isinstance(period, str) and '-' in period:
                    from_str, to_str = period.split('-')
                    period = {'from': from_str.strip(), 'to': to_str.strip()}
                elif not isinstance(period, dict):
                    continue
                try:
                    period_start = datetime.datetime.combine(
                        current.date(),
                        datetime.datetime.strptime(period['from'], "%H:%M").time(),
                        tzinfo=URUGUAY_TZ
                    )
                    period_end = datetime.datetime.combine(
                        current.date(),
                        datetime.datetime.strptime(period['to'], "%H:%M").time(),
                        tzinfo=URUGUAY_TZ
                    )

                    slot = period_start
                    while slot + datetime.timedelta(minutes=duration_minutes) <= period_end:
                        slot_end = slot + datetime.timedelta(minutes=duration_minutes)
                        overlapping = any(bs < slot_end and be > slot for bs, be in busy)
                        if not overlapping:
                            available.append(slot.strftime('%d/%m %H:%M'))
                        slot += datetime.timedelta(minutes=duration_minutes)
                except Exception as e:
                    continue
        current += datetime.timedelta(days=1)

    return available

def create_event(calendar_id, slot_str, user_phone, service_account_info, duration_minutes=30, nombre_cliente="Cliente"):
    service = build_service(service_account_info)
    today = datetime.datetime.now(tz=URUGUAY_TZ)

    dt = datetime.datetime.strptime(slot_str + f"/{today.year}", "%d/%m %H:%M/%Y").replace(tzinfo=URUGUAY_TZ)
    start_time = dt.isoformat()
    end_time = (dt + datetime.timedelta(minutes=duration_minutes)).isoformat()

    event = {
        'summary': f'Turno para {nombre_cliente}',
        'description': f'Reservado automáticamente para {user_phone}',
        'start': {
            'dateTime': start_time,
            'timeZone': 'America/Montevideo',
        },
        'end': {
            'dateTime': end_time,
            'timeZone': 'America/Montevideo',
        },
    }

    try:
        created = service.events().insert(calendarId=calendar_id, body=event).execute()
        print("✅ Evento creado:", created)
        return created.get('id')
    except Exception as e:
        print("❌ Error al crear evento:", e)
        raise

def eliminar_evento(calendar_id, evento_id, service_account_info):
    service = build_service(service_account_info)
    try:
        service.events().delete(calendarId=calendar_id, eventId=evento_id).execute()
        print("🗑️ Evento eliminado exitosamente.")
        return True
    except Exception as e:
        print("❌ Error al eliminar evento:", e)
        return False

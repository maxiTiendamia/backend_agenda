import datetime
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from api.app.models import Tenant

SCOPES = ['https://www.googleapis.com/auth/calendar']
URUGUAY_TZ = datetime.timezone(datetime.timedelta(hours=-3))  # UTC-3 Montevideo

def build_service(service_account_info):
    creds = service_account.Credentials.from_service_account_info(
        json.loads(service_account_info),
        scopes=SCOPES
    )
    return build('calendar', 'v3', credentials=creds)

def get_available_slots(
    calendar_id,
    credentials_json,
    working_hours_json,
    service_duration,
    intervalo_entre_turnos=20,
    max_days=14,
    max_turnos=25,
    cantidad=1 ,
    solo_horas_exactas=False
):
    service = build_service(credentials_json)
    now = datetime.datetime.now(tz=URUGUAY_TZ)
    end_date = now + datetime.timedelta(days=max_days)

    # Obtener eventos ocupados
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
            # Si no tiene tzinfo, agrégala
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=URUGUAY_TZ)
            else:
                start_dt = start_dt.astimezone(URUGUAY_TZ)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=URUGUAY_TZ)
            else:
                end_dt = end_dt.astimezone(URUGUAY_TZ)
            busy.append((start_dt, end_dt))

    # Parsear y normalizar horarios laborales .
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
    turnos_generados = 0
    current_date = now.date()

    while current_date < end_date.date() and turnos_generados < max_turnos:
        day_str = current_date.strftime('%A').lower()
        if day_str in working_hours:
            for period in working_hours[day_str]:
                from_str, to_str = period.split('-')
                start_hour = datetime.datetime.combine(current_date, datetime.datetime.strptime(from_str, '%H:%M').time()).replace(tzinfo=URUGUAY_TZ)
                end_hour = datetime.datetime.combine(current_date, datetime.datetime.strptime(to_str, '%H:%M').time()).replace(tzinfo=URUGUAY_TZ)
                
                # Si es hoy, el primer turno debe ser al menos dentro de 20 minutos
                if current_date == now.date():
                    min_start = now + datetime.timedelta(minutes=20)
                    slot_start = max(start_hour, min_start)
                else:
                    slot_start = start_hour

                slot_end = end_hour
                delta = datetime.timedelta(minutes=service_duration + intervalo_entre_turnos)
                
                while slot_start + datetime.timedelta(minutes=service_duration) <= slot_end:
                    if solo_horas_exactas:
                        minutos = slot_start.minute
                        if minutos not in (0, 30):  # o (0, 15, 30, 45) según lo que quieras
                            slot_start += delta
                            continue
                    # No ofrecer turnos que empiecen antes de 20 minutos desde ahora
                    if slot_start < now + datetime.timedelta(minutes=20):
                        slot_start += delta
                        continue

                    slot_final = slot_start + datetime.timedelta(minutes=service_duration)
                    overlap_count = sum(
                        b_start < slot_final and b_end > slot_start for b_start, b_end in busy
                        )

                    # Nuevo filtro: no ofrecer turnos si el anterior ocupado terminó hace menos del intervalo
                    hay_cerca = any(
                        0 < (slot_start - b_end).total_seconds() / 60 < intervalo_entre_turnos
                        for b_start, b_end in busy if b_end <= slot_start
                    )

                    if overlap_count < cantidad and not hay_cerca:
                        available.append(slot_start)
                        turnos_generados += 1
                        if turnos_generados >= max_turnos:
                            break
                    slot_start += delta
        current_date += datetime.timedelta(days=1)

    return available

def create_event(calendar_id, slot_dt, user_phone, service_account_info, duration_minutes, client_service):
    service = build_service(service_account_info)
    start_time = slot_dt.isoformat()
    end_time = (slot_dt + datetime.timedelta(minutes=duration_minutes)).isoformat()

    event = {
        'summary': client_service,
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

def cancelar_evento_google(calendar_id, reserva_id, service_account_info):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    try:
        # Si service_account_info es string, conviértelo a dict
        if isinstance(service_account_info, str):
            import json
            service_account_info = json.loads(service_account_info)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/calendar"]
        )
        service = build("calendar", "v3", credentials=credentials)
        service.events().delete(
            calendarId=calendar_id,
            eventId=reserva_id
        ).execute()
        return True
    except Exception as e:
        print("Error al cancelar evento:", e)
        return False
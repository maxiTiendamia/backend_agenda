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
        start_datetime = e['start'].get('dateTime')
        start_date_only = e['start'].get('date')
        end_datetime = e['end'].get('dateTime')
        end_date_only = e['end'].get('date')
        
        if start_datetime and end_datetime:
            # Evento con hora específica
            start_dt = datetime.datetime.fromisoformat(start_datetime.replace('Z', '+00:00'))
            end_dt = datetime.datetime.fromisoformat(end_datetime.replace('Z', '+00:00'))
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
            print(f"📅 Evento con hora específica: {start_dt.strftime('%d/%m %H:%M')} - {end_dt.strftime('%d/%m %H:%M')}")
        elif start_date_only and end_date_only:
            # Evento de todo el día - bloquear desde las 00:00 hasta las 23:59
            start_dt = datetime.datetime.fromisoformat(start_date_only).replace(hour=0, minute=0, second=0, tzinfo=URUGUAY_TZ)
            end_dt = datetime.datetime.fromisoformat(end_date_only).replace(hour=23, minute=59, second=59, tzinfo=URUGUAY_TZ)
            busy.append((start_dt, end_dt))
            print(f"📅 Evento de todo el día detectado: {start_date_only} - Bloqueando día completo")

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

                # Si es hoy y el horario de inicio ya pasó, el primer turno debe ser al menos dentro de 20 minutos
                if current_date == now.date() and start_hour < now + datetime.timedelta(minutes=20):
                    slot_start = now + datetime.timedelta(minutes=20)
                    if slot_start < start_hour:
                        slot_start = start_hour
                    
                    # Si se requieren solo horas exactas, redondear al próximo horario válido
                    if solo_horas_exactas:
                        # Redondear hacia arriba al próximo horario en punto o media hora
                        if slot_start.minute <= 30:
                            if slot_start.minute == 0:
                                pass  # Ya está en punto
                            else:
                                slot_start = slot_start.replace(minute=30, second=0, microsecond=0)
                        else:
                            slot_start = slot_start.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
                else:
                    slot_start = start_hour

                slot_end = end_hour
                delta = datetime.timedelta(minutes=service_duration + intervalo_entre_turnos)

                while slot_start + datetime.timedelta(minutes=service_duration) <= slot_end:
                    # No ofrecer turnos que empiecen antes de la hora actual + 20 minutos solo si es hoy y ya pasó el horario de inicio
                    if current_date == now.date() and slot_start < now + datetime.timedelta(minutes=20):
                        slot_start += datetime.timedelta(minutes=30 if solo_horas_exactas else service_duration + intervalo_entre_turnos)
                        continue

                    slot_final = slot_start + datetime.timedelta(minutes=service_duration)
                    overlap_count = sum(
                        b_start < slot_final and b_end > slot_start for b_start, b_end in busy
                    )
                    
                    # Debug: Log de verificación de solapamientos
                    if current_date.strftime('%d/%m') == '31/07':
                        print(f"🔍 Verificando slot {slot_start.strftime('%d/%m %H:%M')} - {slot_final.strftime('%d/%m %H:%M')}")
                        for b_start, b_end in busy:
                            if b_start.date() == current_date or b_end.date() == current_date:
                                overlap = b_start < slot_final and b_end > slot_start
                                print(f"    Evento: {b_start.strftime('%d/%m %H:%M')} - {b_end.strftime('%d/%m %H:%M')} | Solapamiento: {overlap}")
                        print(f"    Total overlaps: {overlap_count}")
                    
                    if overlap_count >= cantidad:
                        # Ya hay suficientes reservas en este horario, avanzar al siguiente slot
                        if solo_horas_exactas:
                            if slot_start.minute == 0:
                                slot_start = slot_start.replace(minute=30)
                            else:
                                slot_start = slot_start.replace(minute=0) + datetime.timedelta(hours=1)
                        else:
                            slot_start += delta
                        continue

                    hay_cerca = any(
                        0 < (slot_start - b_end).total_seconds() / 60 < intervalo_entre_turnos
                        for b_start, b_end in busy if b_end <= slot_start
                    )

                    if overlap_count < cantidad and not hay_cerca:
                        available.append(slot_start)
                        turnos_generados += 1
                        if turnos_generados >= max_turnos:
                            print(f"🔹 Se alcanzó el máximo de turnos: {max_turnos}")
                            break

                    # Avanza al próximo horario exacto (en punto o y media)
                    if solo_horas_exactas:
                        if slot_start.minute == 0:
                            slot_start = slot_start.replace(minute=30)
                        else:
                            slot_start = slot_start.replace(minute=0) + datetime.timedelta(hours=1)
                    else:
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
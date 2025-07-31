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

    # Parsear y normalizar horarios laborales
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
        print(f"🔍 Procesando día: {current_date.strftime('%d/%m')} ({day_str})")
        print(f"🔍 Horarios disponibles para {day_str}: {working_hours.get(day_str, 'No definido')}")
        
        if day_str in working_hours:
            for period in working_hours[day_str]:
                # Corregir el parsing de los horarios
                if isinstance(period, dict):
                    from_str = period['from']
                    to_str = period['to']
                else:
                    # Si es string con formato "08:00-00:00"
                    from_str, to_str = period.split('-')
                
                print(f"🔍 Procesando período: {from_str} - {to_str}")
                
                try:
                    start_hour = datetime.datetime.combine(current_date, datetime.datetime.strptime(from_str, '%H:%M').time()).replace(tzinfo=URUGUAY_TZ)
                    
                    # Manejar horarios que van hasta medianoche (00:00)
                    if to_str == '00:00':
                        # Si termina a medianoche, usar 23:59 del mismo día
                        end_hour = datetime.datetime.combine(current_date, datetime.time(23, 59)).replace(tzinfo=URUGUAY_TZ)
                    else:
                        end_time = datetime.datetime.strptime(to_str, '%H:%M').time()
                        # Si la hora de fin es menor que la de inicio, es del día siguiente
                        if end_time < datetime.datetime.strptime(from_str, '%H:%M').time():
                            end_hour = datetime.datetime.combine(current_date + datetime.timedelta(days=1), end_time).replace(tzinfo=URUGUAY_TZ)
                        else:
                            end_hour = datetime.datetime.combine(current_date, end_time).replace(tzinfo=URUGUAY_TZ)
                    
                    print(f"🔍 Horario calculado: {start_hour.strftime('%d/%m %H:%M')} - {end_hour.strftime('%d/%m %H:%M')}")
                    
                except ValueError as e:
                    print(f"❌ Error al parsear horarios: {e}")
                    continue
                
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

                print(f"🔍 Buscando slots desde {slot_start.strftime('%d/%m %H:%M')} hasta {slot_end.strftime('%d/%m %H:%M')}")
                slots_encontrados_periodo = 0

                while slot_start + datetime.timedelta(minutes=service_duration) <= slot_end:
                    # No ofrecer turnos que empiecen antes de la hora actual + 20 minutos solo si es hoy y ya pasó el horario de inicio
                    if current_date == now.date() and slot_start < now + datetime.timedelta(minutes=20):
                        if solo_horas_exactas:
                            if slot_start.minute == 0:
                                slot_start = slot_start.replace(minute=30)
                            else:
                                slot_start = slot_start.replace(minute=0) + datetime.timedelta(hours=1)
                        else:
                            slot_start += delta
                        continue

                    slot_final = slot_start + datetime.timedelta(minutes=service_duration)
                    
                    # Verificar que el turno no se extienda más allá del horario de cierre
                    # Cambiar <= por < para permitir que termine exactamente al horario de cierre
                    if slot_final > slot_end:
                        break
                    
                    overlap_count = sum(
                        b_start < slot_final and b_end > slot_start for b_start, b_end in busy
                    )
                    
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
                        slots_encontrados_periodo += 1
                        print(f"✅ Slot agregado: {slot_start.strftime('%d/%m %H:%M')} - Termina: {slot_final.strftime('%H:%M')} (Total: {turnos_generados})")
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
                
                print(f"🔍 Slots encontrados en este período: {slots_encontrados_periodo}")
                
                if turnos_generados >= max_turnos:
                    break
        else:
            print(f"❌ No hay horarios definidos para {day_str}")
            
        current_date += datetime.timedelta(days=1)

    print(f"🔍 Total de turnos disponibles encontrados: {len(available)}")
    return available

def get_available_slots_for_service(
    servicio,  # 🆕 Objeto Servicio completo en lugar de parámetros separados
    intervalo_entre_turnos=20,
    max_days=14,
    max_turnos=25,
    credentials_json=None
):
    """
    Obtiene slots disponibles para un servicio específico usando su calendario y horarios
    """
    if not servicio.calendar_id:
        print(f"❌ Servicio {servicio.nombre} no tiene calendar_id configurado")
        return []
    
    if not servicio.working_hours:
        print(f"❌ Servicio {servicio.nombre} no tiene working_hours configurado")
        return []

    service = build_service(credentials_json)
    now = datetime.datetime.now(tz=URUGUAY_TZ)
    end_date = now + datetime.timedelta(days=max_days)

    # Obtener eventos ocupados del calendario del servicio
    events_result = service.events().list(
        calendarId=servicio.calendar_id,
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
            # Evento de todo el día
            start_dt = datetime.datetime.fromisoformat(start_date_only).replace(hour=0, minute=0, second=0, tzinfo=URUGUAY_TZ)
            end_dt = datetime.datetime.fromisoformat(end_date_only).replace(hour=23, minute=59, second=59, tzinfo=URUGUAY_TZ)
            busy.append((start_dt, end_dt))
            print(f"📅 Evento de todo el día detectado: {start_date_only} - Bloqueando día completo")

    # Parsear horarios laborales del servicio
    working_hours_json = servicio.working_hours
    if isinstance(working_hours_json, str):
        try:
            working_hours = json.loads(working_hours_json)
        except json.JSONDecodeError:
            print(f"❌ Error parseando working_hours para servicio {servicio.nombre}")
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

    print(f"🔍 Generando slots para servicio: {servicio.nombre}")
    print(f"🔍 Duración: {servicio.duracion} min, Cantidad: {servicio.cantidad}, Horas exactas: {servicio.solo_horas_exactas}")

    while current_date < end_date.date() and turnos_generados < max_turnos:
        day_str = current_date.strftime('%A').lower()
        print(f"🔍 Procesando día: {current_date.strftime('%d/%m')} ({day_str})")
        print(f"🔍 Horarios disponibles para {day_str}: {working_hours.get(day_str, 'No definido')}")
        
        if day_str in working_hours:
            for period in working_hours[day_str]:
                if isinstance(period, dict):
                    from_str = period['from']
                    to_str = period['to']
                else:
                    from_str, to_str = period.split('-')
                
                print(f"🔍 Procesando período: {from_str} - {to_str}")
                
                try:
                    start_hour = datetime.datetime.combine(current_date, datetime.datetime.strptime(from_str, '%H:%M').time()).replace(tzinfo=URUGUAY_TZ)
                    
                    if to_str == '00:00':
                        end_hour = datetime.datetime.combine(current_date, datetime.time(23, 59)).replace(tzinfo=URUGUAY_TZ)
                    else:
                        end_time = datetime.datetime.strptime(to_str, '%H:%M').time()
                        if end_time < datetime.datetime.strptime(from_str, '%H:%M').time():
                            end_hour = datetime.datetime.combine(current_date + datetime.timedelta(days=1), end_time).replace(tzinfo=URUGUAY_TZ)
                        else:
                            end_hour = datetime.datetime.combine(current_date, end_time).replace(tzinfo=URUGUAY_TZ)
                    
                    print(f"🔍 Horario calculado: {start_hour.strftime('%d/%m %H:%M')} - {end_hour.strftime('%d/%m %H:%M')}")
                    
                except ValueError as e:
                    print(f"❌ Error al parsear horarios: {e}")
                    continue
                
                # Si es hoy y el horario de inicio ya pasó
                if current_date == now.date() and start_hour < now + datetime.timedelta(minutes=20):
                    slot_start = now + datetime.timedelta(minutes=20)
                    if slot_start < start_hour:
                        slot_start = start_hour
                    
                    if servicio.solo_horas_exactas:
                        if slot_start.minute <= 30:
                            if slot_start.minute == 0:
                                pass
                            else:
                                slot_start = slot_start.replace(minute=30, second=0, microsecond=0)
                        else:
                            slot_start = slot_start.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
                else:
                    slot_start = start_hour

                slot_end = end_hour
                delta = datetime.timedelta(minutes=servicio.duracion + intervalo_entre_turnos)

                print(f"🔍 Buscando slots desde {slot_start.strftime('%d/%m %H:%M')} hasta {slot_end.strftime('%d/%m %H:%M')}")
                slots_encontrados_periodo = 0

                while slot_start + datetime.timedelta(minutes=servicio.duracion) <= slot_end:
                    if current_date == now.date() and slot_start < now + datetime.timedelta(minutes=20):
                        if servicio.solo_horas_exactas:
                            if slot_start.minute == 0:
                                slot_start = slot_start.replace(minute=30)
                            else:
                                slot_start = slot_start.replace(minute=0) + datetime.timedelta(hours=1)
                        else:
                            slot_start += delta
                        continue

                    slot_final = slot_start + datetime.timedelta(minutes=servicio.duracion)
                    
                    if slot_final > slot_end:
                        break
                    
                    overlap_count = sum(
                        b_start < slot_final and b_end > slot_start for b_start, b_end in busy
                    )
                    
                    if overlap_count >= (servicio.cantidad or 1):
                        if servicio.solo_horas_exactas:
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

                    if overlap_count < (servicio.cantidad or 1) and not hay_cerca:
                        available.append(slot_start)
                        turnos_generados += 1
                        slots_encontrados_periodo += 1
                        print(f"✅ Slot agregado: {slot_start.strftime('%d/%m %H:%M')} - Termina: {slot_final.strftime('%H:%M')} (Total: {turnos_generados})")
                        if turnos_generados >= max_turnos:
                            print(f"🔹 Se alcanzó el máximo de turnos: {max_turnos}")
                            break

                    if servicio.solo_horas_exactas:
                        if slot_start.minute == 0:
                            slot_start = slot_start.replace(minute=30)
                        else:
                            slot_start = slot_start.replace(minute=0) + datetime.timedelta(hours=1)
                    else:
                        slot_start += delta
                
                print(f"🔍 Slots encontrados en este período: {slots_encontrados_periodo}")
                
                if turnos_generados >= max_turnos:
                    break
        else:
            print(f"❌ No hay horarios definidos para {day_str}")
            
        current_date += datetime.timedelta(days=1)

    print(f"🔍 Total de turnos disponibles encontrados para {servicio.nombre}: {len(available)}")
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

def create_event_for_service(calendar_id, slot_dt, user_phone, service_account_info, duration_minutes, client_service, servicio_nombre):
    """
    Crea un evento específico para un servicio en Google Calendar
    """
    try:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
        import pytz
        from datetime import timedelta
        
        # Crear credenciales
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        
        service = build('calendar', 'v3', credentials=credentials)
        
        # Configurar zona horaria
        argentina_tz = pytz.timezone('America/Argentina/Buenos_Aires')
        
        # Asegurar que slot_dt tenga zona horaria
        if slot_dt.tzinfo is None:
            slot_dt = argentina_tz.localize(slot_dt)
        
        # Calcular fin del evento
        end_time = slot_dt + timedelta(minutes=duration_minutes)
        
        # Crear evento
        event = {
            'summary': f'{servicio_nombre} - {client_service}',
            'description': f'Cliente: {client_service}\nTeléfono: {user_phone}\nServicio: {servicio_nombre}',
            'start': {
                'dateTime': slot_dt.isoformat(),
                'timeZone': 'America/Argentina/Buenos_Aires',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'America/Argentina/Buenos_Aires',
            },
            'attendees': [
                {'email': user_phone + '@placeholder.com'},
            ],
        }
        
        # Insertar evento
        event_result = service.events().insert(
            calendarId=calendar_id,
            body=event
        ).execute()
        
        print(f"✅ Evento creado para servicio: {event_result.get('id')}")
        return event_result.get('id')
        
    except Exception as e:
        print(f"❌ Error creando evento para servicio: {e}")
        raise e
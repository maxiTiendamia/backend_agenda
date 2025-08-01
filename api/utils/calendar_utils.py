# 🔥 CORREGIR IMPORTS - CONFLICTO CON datetime
import json
from datetime import datetime, timedelta, time, date  # 🔥 IMPORT ESPECÍFICO
from google.oauth2 import service_account
from googleapiclient.discovery import build
# 🔥 CAMBIAR A IMPORT ABSOLUTO
from app.models import Tenant
import pytz

SCOPES = ['https://www.googleapis.com/auth/calendar']

def build_service(service_account_info):
    if isinstance(service_account_info, str):
        creds = service_account.Credentials.from_service_account_info(
            json.loads(service_account_info),
            scopes=SCOPES
        )
    else:
        creds = service_account.Credentials.from_service_account_info(
            service_account_info,
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
    max_turnos=30,
    cantidad=1,
    solo_horas_exactas=False,
    turnos_consecutivos=False
):
    """
    Obtiene slots disponibles para un empleado específico
    🔥 CORREGIR: imports y formato de horarios
    """
    try:
        print(f"🔧 DEBUG: get_available_slots - consecutivos: {turnos_consecutivos}, solo_exactas: {solo_horas_exactas}")
        
        # Configurar credenciales de Google
        service_account_info = json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        
        calendar_service = build('calendar', 'v3', credentials=credentials)
        
        # Parsear horarios de trabajo
        working_hours = json.loads(working_hours_json)
        
        # Configurar zona horaria - 🔥 USAR datetime correcto
        tz = pytz.timezone("America/Montevideo")
        now = datetime.now(tz)  # 🔥 FUNCIONA PORQUE IMPORTAMOS datetime correctamente
        end_date = now + timedelta(days=max_days)
        
        all_slots = []
        current_date = now.date()
        
        while current_date <= end_date.date() and len(all_slots) < max_turnos:
            day_name = current_date.strftime('%A').lower()
            day_mapping = {
                'monday': 'monday', 'tuesday': 'tuesday', 'wednesday': 'wednesday',
                'thursday': 'thursday', 'friday': 'friday', 'saturday': 'saturday', 'sunday': 'sunday'
            }
            
            day_key = day_mapping.get(day_name, day_name)
            
            if day_key in working_hours and working_hours[day_key]:
                for period in working_hours[day_key]:
                    if isinstance(period, dict) and 'from' in period and 'to' in period:
                        start_time_str = period['from']
                        end_time_str = period['to']
                        
                        start_hour, start_minute = map(int, start_time_str.split(':'))
                        end_hour, end_minute = map(int, end_time_str.split(':'))
                        
                        period_start = tz.localize(datetime.combine(current_date, time(start_hour, start_minute)))
                        period_end = tz.localize(datetime.combine(current_date, time(end_hour, end_minute)))
                        
                        if period_end <= period_start:
                            period_end += timedelta(days=1)
                        
                        # 🆕 GENERAR SLOTS SEGÚN EL TIPO
                        if turnos_consecutivos:
                            current_slot_start = period_start
                            
                            while current_slot_start + timedelta(minutes=service_duration) <= period_end:
                                if current_slot_start > now:
                                    if is_slot_available_in_calendar(calendar_service, calendar_id, current_slot_start, service_duration):
                                        all_slots.append(current_slot_start)
                                
                                current_slot_start += timedelta(minutes=service_duration)
                                
                        elif solo_horas_exactas:
                            print("🔧 DEBUG: Empleado - Generando solo horas exactas (incluyendo medias horas)")
                            current_time = period_start.replace(minute=0, second=0, microsecond=0)
                            
                            # Si el período empieza después de una hora exacta, ir a la siguiente
                            if current_time < period_start:
                                current_time += timedelta(hours=1)
                            
                            # 🔥 NUEVA LÓGICA: Generar slots cada 30 minutos
                            while current_time + timedelta(minutes=service_duration) <= period_end:
                                # Solo agregar si está en punto de hora (00) o media hora (30)
                                if current_time.minute in [0, 30]:
                                    if current_time > now:
                                        print(f"🔧 DEBUG: Empleado - Verificando slot hora exacta/media: {current_time.strftime('%H:%M')}")
                                        
                                        # 🔥 PERMITIR SOLAPAMIENTO: No verificar Google Calendar
                                        all_slots.append(current_time)
                                        print(f"   ✅ Empleado - Slot hora exacta/media agregado: {current_time.strftime('%d/%m %H:%M')} (con solapamiento)")
                                
                                # Avanzar cada 30 minutos
                                current_time += timedelta(minutes=30)
                                
                        else:
                            current_slot_start = period_start
                            
                            while current_slot_start + timedelta(minutes=service_duration) <= period_end:
                                if current_slot_start > now:
                                    if is_slot_available_in_calendar(calendar_service, calendar_id, current_slot_start, service_duration):
                                        all_slots.append(current_slot_start)
                                
                                current_slot_start += timedelta(minutes=intervalo_entre_turnos)
            
            current_date += timedelta(days=1)
        
        unique_slots = list(set(all_slots))
        unique_slots.sort()
        
        return unique_slots[:max_turnos]
        
    except Exception as e:
        print(f"❌ Error generando slots: {e}")
        return []

def get_available_slots_for_service(
    servicio,
    intervalo_entre_turnos=20,
    max_days=14,
    max_turnos=20,
    credentials_json=""
):
    """
    🔥 CORREGIR: Formato de working_hours y imports
    """
    try:
        print(f"🔧 DEBUG: Generando slots para servicio '{servicio.nombre}'")
        
        # Configurar credenciales de Google
        if not credentials_json:
            raise ValueError("No se proporcionaron credenciales de Google")
            
        service_account_info = json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        
        calendar_service = build('calendar', 'v3', credentials=credentials)
        
        # Parsear horarios de trabajo
        working_hours = json.loads(servicio.working_hours)
        service_duration = servicio.duracion
        
        # 🆕 VERIFICAR TIPO DE SERVICIO
        turnos_consecutivos = getattr(servicio, 'turnos_consecutivos', False)
        solo_horas_exactas = getattr(servicio, 'solo_horas_exactas', False)
        
        print(f"🔧 DEBUG: Configuración del servicio:")
        print(f"   - Duración: {service_duration} min")
        print(f"   - Turnos consecutivos: {turnos_consecutivos}")
        print(f"   - Solo horas exactas: {solo_horas_exactas}")
        print(f"   - Intervalo entre turnos: {intervalo_entre_turnos} min")
        print(f"   - Working hours RAW: {working_hours}")
        
        # Configurar zona horaria - 🔥 CORREGIR IMPORT
        tz = pytz.timezone("America/Montevideo")
        now = datetime.now(tz)  # 🔥 AHORA FUNCIONA
        end_date = now + timedelta(days=max_days)
        
        all_slots = []
        current_date = now.date()
        
        while current_date <= end_date.date() and len(all_slots) < max_turnos:
            day_name = current_date.strftime('%A').lower()
            
            day_mapping = {
                'monday': 'monday', 'tuesday': 'tuesday', 'wednesday': 'wednesday',
                'thursday': 'thursday', 'friday': 'friday', 'saturday': 'saturday', 'sunday': 'sunday'
            }
            
            day_key = day_mapping.get(day_name, day_name)
            print(f"🔧 DEBUG: Procesando día {current_date} ({day_key}) - Slots actuales: {len(all_slots)}")
            
            if day_key in working_hours and working_hours[day_key]:
                day_periods = working_hours[day_key]
                print(f"🔧 DEBUG: Períodos para {day_key}: {day_periods}")
                
                # 🔥 DETECTAR Y CONVERTIR FORMATO INCORRECTO
                if isinstance(day_periods, list) and len(day_periods) > 0:
                    # Verificar si es formato string "08:00-15:00"
                    if isinstance(day_periods[0], str) and '-' in day_periods[0]:
                        print(f"🔧 DEBUG: Detectado formato string '{day_periods[0]}', convirtiendo...")
                        converted_periods = []
                        for period_str in day_periods:
                            if '-' in period_str and period_str != "--:---:--":
                                start_str, end_str = period_str.split('-')
                                converted_periods.append({
                                    'from': start_str.strip(),
                                    'to': end_str.strip()
                                })
                                print(f"🔧 DEBUG: Convertido '{period_str}' a {{'from': '{start_str.strip()}', 'to': '{end_str.strip()}'}}")
                        day_periods = converted_periods
                        print(f"🔧 DEBUG: Períodos convertidos: {day_periods}")
                    
                    # Si ya es formato dict, mantenerlo
                    elif isinstance(day_periods[0], dict):
                        pass  # Ya está en el formato correcto
                    else:
                        print(f"⚠️ Formato no reconocido: {day_periods}")
                        current_date += timedelta(days=1)
                        continue
                        
                elif isinstance(day_periods, dict):
                    # Formato viejo: {"from": "08:00", "to": "15:00"}
                    day_periods = [day_periods]
                else:
                    print(f"⚠️ Formato de horarios no reconocido para {day_key}: {day_periods}")
                    current_date += timedelta(days=1)
                    continue
                
                for period in day_periods:
                    if isinstance(period, dict) and 'from' in period and 'to' in period:
                        start_time_str = period['from']
                        end_time_str = period['to']
                        
                        print(f"🔧 DEBUG: Procesando período {start_time_str}-{end_time_str}")
                        
                        # 🔥 VALIDAR FORMATO DE HORAS
                        if start_time_str == "--:--" or end_time_str == "--:--":
                            print(f"🔧 DEBUG: Período inválido ignorado: {start_time_str}-{end_time_str}")
                            continue
                        
                        try:
                            # Parsear horas
                            start_hour, start_minute = map(int, start_time_str.split(':'))
                            end_hour, end_minute = map(int, end_time_str.split(':'))
                            
                            period_start = tz.localize(datetime.combine(current_date, time(start_hour, start_minute)))
                            period_end = tz.localize(datetime.combine(current_date, time(end_hour, end_minute)))
                            
                            # Si es al día siguiente (ej: 22:00 a 02:00)
                            if period_end <= period_start:
                                period_end += timedelta(days=1)
                            
                            print(f"🔧 DEBUG: Período convertido: {period_start} a {period_end}")
                            
                            # 🔥 SOLO PROCESAR SI EL PERÍODO ES EN EL FUTURO
                            if period_end <= now:
                                print(f"🔧 DEBUG: Período {period_start}-{period_end} ya pasó, saltando")
                                continue
                            
                            # Ajustar inicio si es en el pasado
                            if period_start <= now:
                                print(f"🔧 DEBUG: Ajustando período que empezó en el pasado: {period_start}")
                                # Redondear al siguiente intervalo válido
                                minutes_diff = (now - period_start).total_seconds() / 60
                                if turnos_consecutivos:
                                    intervals_passed = int(minutes_diff / service_duration) + 1
                                    period_start = period_start + timedelta(minutes=intervals_passed * service_duration)
                                elif solo_horas_exactas:
                                    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                                    period_start = max(period_start, next_hour)
                                else:
                                    intervals_passed = int(minutes_diff / intervalo_entre_turnos) + 1
                                    period_start = period_start + timedelta(minutes=intervals_passed * intervalo_entre_turnos)
                                
                                print(f"🔧 DEBUG: Período ajustado al futuro: {period_start}")
                            

                            # 🆕 GENERAR SLOTS SEGÚN EL TIPO DE SERVICIO
                            if turnos_consecutivos:
                                print("🔧 DEBUG: Generando turnos consecutivos")
                                current_slot_start = period_start
                                slots_for_day = 0  # Contador para debug
                                
                                while current_slot_start + timedelta(minutes=service_duration) <= period_end:
                                    slot_end = current_slot_start + timedelta(minutes=service_duration)
                                    
                                    # Verificar que el slot completo esté en el futuro
                                    if current_slot_start > now:
                                        print(f"🔧 DEBUG: Verificando slot consecutivo: {current_slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')} ({service_duration}min)")
                                        
                                        # Verificar disponibilidad en Google Calendar
                                        if is_slot_available_in_calendar(calendar_service, servicio.calendar_id, current_slot_start, service_duration):
                                            all_slots.append(current_slot_start)
                                            slots_for_day += 1
                                            print(f"   ✅ Slot consecutivo agregado: {current_slot_start.strftime('%d/%m %H:%M')}-{slot_end.strftime('%H:%M')}")
                                        else:
                                            print(f"   ❌ Slot consecutivo ocupado: {current_slot_start.strftime('%d/%m %H:%M')}-{slot_end.strftime('%H:%M')}")
                                    else:
                                        print(f"🔧 DEBUG: Slot en el pasado saltado: {current_slot_start.strftime('%H:%M')}")
                                    
                                    # 🔑 CLAVE: Avanzar exactamente por la duración del servicio (sin solapamiento)
                                    current_slot_start += timedelta(minutes=service_duration)
                                
                                print(f"🔧 DEBUG: Día {current_date} - {slots_for_day} slots consecutivos generados")
                                    
                            elif solo_horas_exactas:
                                print("🔧 DEBUG: Generando solo horas exactas (incluyendo medias horas)")
                                # Empezar desde la hora exacta más cercana
                                current_time = period_start.replace(minute=0, second=0, microsecond=0)
                                
                                # Si el período empieza después de una hora exacta, ir a la siguiente
                                if current_time < period_start:
                                    current_time += timedelta(hours=1)
                                
                                slots_for_day = 0
                                
                                # 🔥 NUEVA LÓGICA: Generar slots cada 30 minutos (horas exactas Y medias horas)
                                while current_time + timedelta(minutes=service_duration) <= period_end:
                                    slot_end = current_time + timedelta(minutes=service_duration)
                                    
                                    # Solo agregar si está en punto de hora (00) o media hora (30)
                                    if current_time.minute in [0, 30]:
                                        if current_time > now:
                                            print(f"🔧 DEBUG: Verificando slot hora exacta/media: {current_time.strftime('%H:%M')}-{slot_end.strftime('%H:%M')} ({service_duration}min)")
                                            
                                            # 🔥 PERMITIR SOLAPAMIENTO: No verificar Google Calendar, solo agregar
                                            # En "solo horas exactas" se permite solapamiento
                                            all_slots.append(current_time)
                                            slots_for_day += 1
                                            print(f"   ✅ Slot hora exacta/media agregado: {current_time.strftime('%d/%m %H:%M')}-{slot_end.strftime('%H:%M')} (con solapamiento permitido)")
                                        else:
                                            print(f"🔧 DEBUG: Slot hora exacta/media en el pasado: {current_time.strftime('%H:%M')}")
                                    
                                    # 🔑 AVANZAR CADA 30 MINUTOS para cubrir horas exactas Y medias horas
                                    current_time += timedelta(minutes=30)
                                
                                print(f"🔧 DEBUG: Día {current_date} - {slots_for_day} slots de horas exactas/medias generados")
                                    
                            else:
                                print("🔧 DEBUG: Generando turnos normales")
                                current_slot_start = period_start
                                slots_for_day = 0
                                
                                while current_slot_start + timedelta(minutes=service_duration) <= period_end:
                                    slot_end = current_slot_start + timedelta(minutes=service_duration)
                                    
                                    if current_slot_start > now:
                                        print(f"🔧 DEBUG: Verificando slot normal: {current_slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')} (intervalo: {intervalo_entre_turnos}min)")
                                        
                                        if is_slot_available_in_calendar(calendar_service, servicio.calendar_id, current_slot_start, service_duration):
                                            all_slots.append(current_slot_start)
                                            slots_for_day += 1
                                            print(f"   ✅ Slot normal agregado: {current_slot_start.strftime('%d/%m %H:%M')}-{slot_end.strftime('%H:%M')}")
                                        else:
                                            print(f"   ❌ Slot normal ocupado: {current_slot_start.strftime('%d/%m %H:%M')}-{slot_end.strftime('%H:%M')}")
                                    else:
                                        print(f"🔧 DEBUG: Slot normal en el pasado: {current_slot_start.strftime('%H:%M')}")
                                    
                                    # Avanzar por el intervalo configurado
                                    current_slot_start += timedelta(minutes=intervalo_entre_turnos)
                                
                                print(f"🔧 DEBUG: Día {current_date} - {slots_for_day} slots normales generados")
                            

                        except Exception as period_error:
                            print(f"❌ Error procesando período {start_time_str}-{end_time_str}: {period_error}")
                            import traceback
                            traceback.print_exc()
                            continue
            else:
                print(f"🔧 DEBUG: No hay horarios configurados para {day_key}")
            
            current_date += timedelta(days=1)
            
            # 🔥 DEBUG: Si es lunes y no genera slots, verificar
            if day_key == 'monday' and len(all_slots) == 0:
                print(f"⚠️ WARNING: Es lunes pero no se generaron slots. Working hours: {working_hours}")
        
        # Ordenar y retornar slots únicos
        unique_slots = list(set(all_slots))
        unique_slots.sort()
        
        print(f"🔧 DEBUG: Total slots únicos generados: {len(unique_slots)}")
        for i, slot in enumerate(unique_slots[:5]):
            print(f"   {i+1}. {slot.strftime('%d/%m %H:%M')}")
        
        return unique_slots[:max_turnos]
        
    except Exception as e:
        print(f"❌ Error generando slots para servicio {servicio.nombre}: {e}")
        import traceback
        traceback.print_exc()
        return []

def is_slot_available_in_calendar(calendar_service, calendar_id, slot_start, duration_minutes):
    """
    Verifica si un slot específico está disponible en Google Calendar
    """
    try:
        slot_end = slot_start + timedelta(minutes=duration_minutes)
        
        # Buscar eventos en Google Calendar en ese rango
        events_result = calendar_service.events().list(
            calendarId=calendar_id,
            timeMin=slot_start.isoformat(),
            timeMax=slot_end.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if events:
            print(f"🔧 DEBUG: Slot {slot_start.strftime('%d/%m %H:%M')} ocupado - {len(events)} eventos encontrados")
            return False
        else:
            print(f"🔧 DEBUG: Slot {slot_start.strftime('%d/%m %H:%M')} disponible - sin conflictos")
            return True
            
    except Exception as e:
        print(f"❌ Error verificando disponibilidad en calendario: {e}")
        # Si hay error, asumir que está disponible para no bloquear el sistema
        return True

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

def create_event_for_service(servicio, slot_dt, user_phone, service_account_info, client_name):
    """
    Crea un evento específico para un servicio en Google Calendar
    🔥 SOLUCIÓN: Remover la sección de attendees que causa el error 403
    """
    try:
        # Crear credenciales
        if isinstance(service_account_info, str):
            credentials = service_account.Credentials.from_service_account_info(
                json.loads(service_account_info),
                scopes=['https://www.googleapis.com/auth/calendar']
            )
        else:
            credentials = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=['https://www.googleapis.com/auth/calendar']
            )
        
        service = build('calendar', 'v3', credentials=credentials)
        
        # Configurar zona horaria
        uruguay_tz = pytz.timezone('America/Montevideo')
        
        # Asegurar que slot_dt tenga zona horaria
        if slot_dt.tzinfo is None:
            slot_dt = uruguay_tz.localize(slot_dt)
        
        # Calcular fin del evento
        end_time = slot_dt + timedelta(minutes=servicio.duracion)
        
        # 🔥 CREAR EVENTO SIN ATTENDEES PARA EVITAR ERROR 403
        event = {
            'summary': f'{servicio.nombre} - {client_name}',
            'description': f'Cliente: {client_name}\nTeléfono: {user_phone}\nServicio: {servicio.nombre}\nDuración: {servicio.duracion} minutos\nPrecio: ${servicio.precio}',
            'start': {
                'dateTime': slot_dt.isoformat(),
                'timeZone': 'America/Montevideo',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'America/Montevideo',
            },
            # 🔥 REMOVER attendees - Esta línea causaba el error 403
            # 'attendees': [
            #     {'email': user_phone + '@placeholder.com'},
            # ],
        }
        
        # Usar calendar_id del servicio
        calendar_id = servicio.calendar_id
        
        if not calendar_id:
            raise Exception("No hay calendar_id configurado para este servicio")
        
        # Insertar evento
        event_result = service.events().insert(
            calendarId=calendar_id,
            body=event
        ).execute()
        
        print(f"✅ Evento creado para servicio: {event_result.get('id')} - {client_name} - {slot_dt.strftime('%d/%m %H:%M')}")
        return event_result.get('id')
        
    except Exception as e:
        print(f"❌ Error creando evento para servicio: {e}")
        raise e
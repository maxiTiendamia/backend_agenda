import datetime
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
# 🔥 CAMBIAR A IMPORT ABSOLUTO
from app.models import Tenant
from datetime import timedelta
import pytz

SCOPES = ['https://www.googleapis.com/auth/calendar']
URUGUAY_TZ = datetime.timezone(datetime.timedelta(hours=-3))  # UTC-3 Montevideo

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
    max_turnos=20,
    cantidad=1 ,
    solo_horas_exactas=False,
    turnos_consecutivos=False
):
    """
    Obtiene slots disponibles para un empleado
    Ahora incluye soporte para turnos consecutivos
    """
    try:
        # Configurar credenciales de Google
        service_account_info = json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        
        calendar_service = build('calendar', 'v3', credentials=credentials)
        
        # Parsear horarios de trabajo
        working_hours = json.loads(working_hours_json)
        
        # Configurar zona horaria
        tz = pytz.timezone("America/Montevideo")
        now = datetime.now(tz)
        end_date = now + timedelta(days=14)
        
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
                            # TURNOS CONSECUTIVOS
                            current_slot_start = period_start
                            
                            while current_slot_start + timedelta(minutes=service_duration) <= period_end:
                                if current_slot_start > now:
                                    if is_slot_available_in_calendar(calendar_service, calendar_id, current_slot_start, service_duration):
                                        all_slots.append(current_slot_start)
                                
                                current_slot_start += timedelta(minutes=service_duration)
                                
                        elif solo_horas_exactas:
                            # SOLO HORAS EXACTAS
                            current_hour = period_start.replace(minute=0, second=0, microsecond=0)
                            
                            while current_hour + timedelta(minutes=service_duration) <= period_end:
                                if current_hour > now:
                                    if is_slot_available_in_calendar(calendar_service, calendar_id, current_hour, service_duration):
                                        all_slots.append(current_hour)
                                
                                current_hour += timedelta(hours=1)
                                
                        else:
                            # TURNOS NORMALES
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
    servicio,  # 🆕 Objeto Servicio completo en lugar de parámetros separados
    intervalo_entre_turnos=20,
    max_days=14,
    max_turnos=20,
    credentials_json=""
):
    """
    Obtiene slots disponibles para un servicio específico
    🔥 MANEJA TODOS LOS TIPOS: normales, consecutivos y horas exactas
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
        
        # Configurar zona horaria
        tz = pytz.timezone("America/Montevideo")
        now = datetime.datetime.now(tz)
        end_date = now + timedelta(days=14)  # 2 semanas adelante
        
        all_slots = []
        current_date = now.date()
        
        while current_date <= end_date.date() and len(all_slots) < max_turnos:
            day_name = current_date.strftime('%A').lower()
            
            # Mapeo de días en inglés
            day_mapping = {
                'monday': 'monday', 'tuesday': 'tuesday', 'wednesday': 'wednesday',
                'thursday': 'thursday', 'friday': 'friday', 'saturday': 'saturday', 'sunday': 'sunday'
            }
            
            day_key = day_mapping.get(day_name, day_name)
            
            if day_key in working_hours and working_hours[day_key]:
                print(f"🔧 DEBUG: Procesando día {day_key} con {len(working_hours[day_key])} períodos")
                
                for period in working_hours[day_key]:
                    if isinstance(period, dict) and 'from' in period and 'to' in period:
                        start_time_str = period['from']
                        end_time_str = period['to']
                        
                        # Parsear horas
                        start_hour, start_minute = map(int, start_time_str.split(':'))
                        end_hour, end_minute = map(int, end_time_str.split(':'))
                        
                        period_start = tz.localize(datetime.datetime.combine(current_date, datetime.time(start_hour, start_minute)))
                        period_end = tz.localize(datetime.datetime.combine(current_date, datetime.time(end_hour, end_minute)))
                        
                        # Si es al día siguiente (ej: 22:00 a 02:00)
                        if period_end <= period_start:
                            period_end += timedelta(days=1)
                        
                        print(f"🔧 DEBUG: Período {start_time_str}-{end_time_str}")
                        
                        # 🆕 GENERAR SLOTS SEGÚN EL TIPO DE SERVICIO
                        if turnos_consecutivos:
                            print("🔧 DEBUG: Generando turnos consecutivos")
                            # TURNOS CONSECUTIVOS: slots de duración exacta sin solapamiento
                            current_slot_start = period_start
                            
                            while current_slot_start + timedelta(minutes=service_duration) <= period_end:
                                # Verificar que el slot esté en el futuro
                                if current_slot_start > now:
                                    # Verificar disponibilidad en Google Calendar
                                    if is_slot_available_in_calendar(calendar_service, servicio.calendar_id, current_slot_start, service_duration):
                                        all_slots.append(current_slot_start)
                                        print(f"   ✅ Slot consecutivo agregado: {current_slot_start.strftime('%d/%m %H:%M')}")
                                
                                # 🔑 CLAVE: Avanzar por la duración completa del servicio (sin solapamiento)
                                current_slot_start += timedelta(minutes=service_duration)
                                
                        elif solo_horas_exactas:
                            print("🔧 DEBUG: Generando solo horas exactas")
                            # SOLO HORAS EXACTAS: solo en punto de hora
                            current_hour = period_start.replace(minute=0, second=0, microsecond=0)
                            
                            while current_hour + timedelta(minutes=service_duration) <= period_end:
                                if current_hour > now:
                                    if is_slot_available_in_calendar(calendar_service, servicio.calendar_id, current_hour, service_duration):
                                        all_slots.append(current_hour)
                                        print(f"   ✅ Slot hora exacta agregado: {current_hour.strftime('%d/%m %H:%M')}")
                                
                                current_hour += timedelta(hours=1)
                                
                        else:
                            print("🔧 DEBUG: Generando turnos normales")
                            # TURNOS NORMALES: con intervalo personalizado
                            current_slot_start = period_start
                            
                            while current_slot_start + timedelta(minutes=service_duration) <= period_end:
                                # Verificar que el slot esté en el futuro
                                if current_slot_start > now:
                                    # Verificar disponibilidad en Google Calendar
                                    if is_slot_available_in_calendar(calendar_service, servicio.calendar_id, current_slot_start, service_duration):
                                        all_slots.append(current_slot_start)
                                        print(f"   ✅ Slot normal agregado: {current_slot_start.strftime('%d/%m %H:%M')}")
                                
                                # Avanzar por el intervalo configurado
                                current_slot_start += timedelta(minutes=intervalo_entre_turnos)
            
            current_date += timedelta(days=1)
        
        # Ordenar y retornar slots únicos
        unique_slots = list(set(all_slots))
        unique_slots.sort()
        
        print(f"🔧 DEBUG: Total slots generados: {len(unique_slots)}")
        for i, slot in enumerate(unique_slots[:5]):  # Mostrar solo los primeros 5
            print(f"   {i+1}. {slot.strftime('%d/%m %H:%M')}")
        
        return unique_slots[:max_turnos]
        
    except Exception as e:
        print(f"❌ Error generando slots para servicio {servicio.nombre}: {e}")
        import traceback
        traceback.print_exc()
        return []

def is_slot_available_in_calendar(calendar_service, calendar_id, slot_start, duration_minutes):
    """
    Verifica si un slot está disponible en Google Calendar
    """
    try:
        slot_end = slot_start + timedelta(minutes=duration_minutes)
        
        # Convertir a formato RFC3339 para la API de Google
        time_min = slot_start.isoformat()
        time_max = slot_end.isoformat()
        
        # Consultar eventos en el rango
        events_result = calendar_service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True
        ).execute()
        
        events = events_result.get('items', [])
        
        # Si hay eventos en el rango, el slot no está disponible
        return len(events) == 0
        
    except Exception as e:
        print(f"❌ Error verificando disponibilidad en calendario: {e}")
        return False

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
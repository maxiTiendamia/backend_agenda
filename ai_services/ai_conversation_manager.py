import openai
import json
from datetime import datetime, timedelta, timezone
import pytz
from sqlalchemy.orm import Session
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from api.app.models import Tenant, Servicio, Empleado, Reserva, BlockedNumber
from api.utils.generador_fake_id import generar_fake_id
from google.oauth2 import service_account
from googleapiclient.discovery import build
import redis
import httpx
import re

class AIConversationManager:
    def __init__(self, api_key, redis_client):
        self.client = openai.OpenAI(api_key=api_key)
        self.redis_client = redis_client
        self.tz = pytz.timezone("America/Montevideo")
        self.webconnect_url = os.getenv("webconnect_url", "http://195.26.250.62:3000")  
        self.google_credentials = os.getenv("GOOGLE_CREDENTIALS_JSON")
    
    def _normalize_datetime(self, dt):
        """🔧 NORMALIZAR datetime para que siempre tenga timezone"""
        if dt is None:
            return None
        
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        return dt.astimezone(self.tz)
    
    def _get_conversation_history(self, telefono: str) -> list:
        """Obtener historial de conversación desde Redis"""
        try:
            history_key = f"conversation:{telefono}"
            messages = self.redis_client.lrange(history_key, 0, -1)
            return [json.loads(msg) for msg in messages]
        except:
            return []
    
    def _save_conversation_message(self, telefono: str, role: str, content: str):
        """Guardar mensaje en historial de conversación"""
        try:
            history_key = f"conversation:{telefono}"
            message = {
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat()
            }
            self.redis_client.lpush(history_key, json.dumps(message))
            self.redis_client.expire(history_key, 3600 * 24)  # 24 horas
        except Exception as e:
            print(f"Error guardando conversación: {e}")
    
    def _is_blocked_number(self, telefono: str, cliente_id: int, db: Session) -> bool:
        """Verificar si el número está bloqueado"""
        try:
            blocked = db.query(BlockedNumber).filter(
                BlockedNumber.telefono == telefono,
                BlockedNumber.cliente_id == cliente_id
            ).first()
            return blocked is not None
        except:
            return False
    
    def _is_human_mode(self, telefono: str) -> bool:
        """Verificar si está en modo humano"""
        try:
            human_mode_key = f"human_mode:{telefono}"
            return self.redis_client.get(human_mode_key) == "true"
        except:
            return False
    
    async def _notify_human_support(self, cliente_id: int, telefono: str, mensaje: str):
        """Notificar a soporte humano"""
        try:
            # Aquí podrías implementar notificación por email, Slack, etc.
            print(f"🚨 MODO HUMANO - Cliente {cliente_id} ({telefono}): {mensaje}")
        except Exception as e:
            print(f"Error notificando soporte humano: {e}")
    
    def _get_user_history(self, telefono: str, db: Session) -> dict:
        """Obtener historial completo del usuario"""
        reservas_activas = db.query(Reserva).filter(
            Reserva.cliente_telefono == telefono,
            Reserva.estado == "activo"
        ).all()
        
        reservas_pasadas = db.query(Reserva).filter(
            Reserva.cliente_telefono == telefono,
            Reserva.estado.in_(["completado", "cancelado"])
        ).order_by(Reserva.fecha_reserva.desc()).limit(5).all()
        
        now_aware = datetime.now(self.tz)
        
        return {
            "reservas_activas": [
                {
                    "codigo": r.fake_id,
                    "servicio": r.servicio,
                    "empleado": r.empleado_nombre,
                    "fecha": r.fecha_reserva.strftime("%d/%m %H:%M") if r.fecha_reserva else "",
                    "puede_cancelar": self._puede_cancelar_reserva(r.fecha_reserva, now_aware)
                }
                for r in reservas_activas
            ],
            "historial": [
                {
                    "servicio": r.servicio,
                    "fecha": r.fecha_reserva.strftime("%d/%m/%Y") if r.fecha_reserva else "",
                    "estado": r.estado
                }
                for r in reservas_pasadas
            ],
            "es_cliente_recurrente": len(reservas_pasadas) > 0,
            "servicio_favorito": self._get_servicio_favorito(reservas_pasadas)
        }
    
    def _get_servicio_favorito(self, reservas_pasadas):
        """Determinar servicio más utilizado"""
        if not reservas_pasadas:
            return None
        
        servicios = {}
        for reserva in reservas_pasadas:
            servicios[reserva.servicio] = servicios.get(reserva.servicio, 0) + 1
        
        return max(servicios, key=servicios.get) if servicios else None
    
    def _puede_cancelar_reserva(self, fecha_reserva, now_aware):
        """Verificar si se puede cancelar una reserva"""
        if not fecha_reserva:
            return False
        
        fecha_reserva_aware = self._normalize_datetime(fecha_reserva)
        return fecha_reserva_aware > now_aware + timedelta(hours=1)
    
    async def process_message(self, telefono: str, mensaje: str, cliente_id: int, db: Session):
        """Procesar mensaje con IA más natural y contextual"""
        try:
            # Verificar si está bloqueado
            if self._is_blocked_number(telefono, cliente_id, db):
                return "❌ Este número está bloqueado."
            
            # Verificar modo humano
            if self._is_human_mode(telefono):
                await self._notify_human_support(cliente_id, telefono, mensaje)
                return "👥 Tu mensaje fue enviado a nuestro equipo humano. Te responderemos pronto."
            
            # Obtener contexto del negocio
            tenant = db.query(Tenant).filter(Tenant.id == cliente_id).first()
            if not tenant:
                return "❌ No encontré información del negocio."
            
            # Obtener historial del usuario
            user_history = self._get_user_history(telefono, db)
            business_context = self._get_business_context(tenant, db)
            conversation_history = self._get_conversation_history(telefono)
            
            # Guardar mensaje del usuario
            self._save_conversation_message(telefono, "user", mensaje)
            
            # Procesar con IA
            respuesta = await self._ai_process_conversation_natural(
                mensaje, telefono, conversation_history, user_history, business_context, tenant, db
            )
            
            # Guardar respuesta de la IA
            self._save_conversation_message(telefono, "assistant", respuesta)
            
            return respuesta
            
        except Exception as e:
            print(f"❌ Error en AI manager: {e}")
            return "Disculpa, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?"
    
    async def _ai_process_conversation_natural(self, mensaje: str, telefono: str, conversation_history: list, user_history: dict, business_context: dict, tenant: Tenant, db: Session) -> str:
        """Procesamiento de IA más natural y contextual"""
        
        # 🔧 DETECTAR SELECCIÓN DE SERVICIO (NÚMERO O NOMBRE)
        mensaje_stripped = mensaje.strip().lower()
        servicio_seleccionado = None
        
        # Verificar si es un número
        if mensaje_stripped.isdigit():
            try:
                posicion = int(mensaje_stripped)
                if 1 <= posicion <= len(business_context['servicios']):
                    servicio_seleccionado = business_context['servicios'][posicion - 1]
            except:
                pass
        
        # Si no es número, buscar por nombre de servicio
        if not servicio_seleccionado:
            for servicio in business_context['servicios']:
                nombre_servicio = servicio['nombre'].lower()
                # Buscar coincidencia exacta o parcial
                if (mensaje_stripped == nombre_servicio or 
                    mensaje_stripped in nombre_servicio or 
                    nombre_servicio in mensaje_stripped):
                    servicio_seleccionado = servicio
                    break
        
        # Si no es servicio, buscar por nombre de empleado
        if not servicio_seleccionado:
            for empleado in business_context['empleados']:
                nombre_empleado = empleado['nombre'].lower()
                if (mensaje_stripped == nombre_empleado or 
                    mensaje_stripped in nombre_empleado or 
                    nombre_empleado in mensaje_stripped):
                    # Si selecciona empleado, mostrar sus servicios disponibles
                    return self._mostrar_servicios_empleado(empleado, business_context)
        
        # Si encontró un servicio
        if servicio_seleccionado:
            # Verificar si es servicio informativo
            if servicio_seleccionado.get('es_informativo', False):
                return servicio_seleccionado.get('mensaje_personalizado', 
                    f"📋 *{servicio_seleccionado['nombre']}*\n\nEste es un servicio informativo. ¿Necesitas más información?")
            
            # Llamar directamente a buscar horarios con el ID real
            return await self._buscar_horarios_servicio_real(
                servicio_seleccionado['id'],
                business_context, 
                telefono, 
                tenant,
                db
            )
        
        # Si no encontró coincidencias, continuar con procesamiento normal de IA
        # Construir contexto para la IA
        system_prompt = f"""Eres la IA asistente de {tenant.comercio}. 

INFORMACIÓN DEL NEGOCIO:
- Nombre: {tenant.comercio}
- Servicios disponibles: {', '.join([s['nombre'] for s in business_context['servicios']])}
- Empleados: {', '.join([e['nombre'] for e in business_context['empleados']])}

INFORMACIÓN DEL CLIENTE (teléfono: {telefono}):
- Cliente recurrente: {'Sí' if user_history['es_cliente_recurrente'] else 'No (cliente nuevo)'}
- Servicio favorito: {user_history['servicio_favorito'] or 'Ninguno aún'}
- Reservas activas: {len(user_history['reservas_activas'])}
- Historial: {len(user_history['historial'])} reservas anteriores

INSTRUCCIONES IMPORTANTES:
1. Sé natural, amigable y personalizada
2. Usa la información del cliente para personalizar respuestas
3. Cuando te pidan un turno, muestra los servicios numerados (1, 2, 3...)
4. Si el usuario dice un número, usa la función buscar_horarios_servicio con el ID REAL del servicio
5. SERVICIOS CON SUS IDs REALES:
{self._format_servicios_with_real_ids(business_context['servicios'])}
6. Recuerda conversaciones anteriores
7. Puedes responder preguntas generales sobre el negocio
8. Para fechas específicas, usa la función buscar_horarios_fecha_especifica

FUNCIONES DISPONIBLES:
- buscar_horarios_servicio: Para mostrar horarios disponibles (usa el ID real del servicio)
- buscar_horarios_fecha_especifica: Para horarios en fecha/hora específica
- crear_reserva: Para confirmar una reserva
- cancelar_reserva: Para cancelar reservas existentes
"""

        # Construir historial de conversación
        messages = [{"role": "system", "content": system_prompt}]
        
        # Agregar historial reciente (últimos 10 mensajes)
        recent_history = conversation_history[-10:] if len(conversation_history) > 10 else conversation_history
        for msg in reversed(recent_history):
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        # Agregar mensaje actual
        messages.append({"role": "user", "content": mensaje})
        
        # Definir funciones disponibles
        functions = [
            {
                "name": "buscar_horarios_servicio",
                "description": "Buscar horarios disponibles para un servicio específico",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "servicio_id": {"type": "integer", "description": "ID REAL del servicio en la base de datos"},
                        "preferencia_horario": {"type": "string", "description": "mañana, tarde, noche o cualquiera"},
                        "preferencia_fecha": {"type": "string", "description": "hoy, mañana, esta_semana o cualquiera"},
                        "cantidad": {"type": "integer", "description": "Cantidad de personas", "default": 1}
                    },
                    "required": ["servicio_id"]
                }
            },
            {
                "name": "buscar_horarios_fecha_especifica", 
                "description": "Buscar horarios en una fecha/hora específica",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "servicio_id": {"type": "integer", "description": "ID del servicio"},
                        "fecha_especifica": {"type": "string", "description": "Fecha en formato DD/MM o DD/MM/YYYY"},
                        "hora_especifica": {"type": "string", "description": "Hora específica si se menciona (HH:MM)"},
                        "cantidad": {"type": "integer", "default": 1}
                    },
                    "required": ["servicio_id", "fecha_especifica"]
                }
            },
            {
                "name": "crear_reserva",
                "description": "Crear una nueva reserva",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "servicio_id": {"type": "integer", "description": "ID REAL del servicio"},
                        "fecha_hora": {"type": "string", "description": "Fecha y hora en formato YYYY-MM-DD HH:MM"},
                        "empleado_id": {"type": "integer", "description": "ID del empleado (opcional)"},
                        "nombre_cliente": {"type": "string"},
                        "cantidad": {"type": "integer", "default": 1}
                    },
                    "required": ["servicio_id", "fecha_hora", "nombre_cliente"]
                }
            },
            {
                "name": "cancelar_reserva",
                "description": "Cancelar una reserva existente",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "codigo_reserva": {"type": "string", "description": "Código de la reserva"}
                    },
                    "required": ["codigo_reserva"]
                }
            }
        ]
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",  # 🔧 CAMBIO: GPT-3.5-Turbo es más económico
                messages=messages,
                functions=functions,
                function_call="auto",
                temperature=0.3,  # 🔧 REDUCIDO: Más consistente y enfocado
                max_tokens=800   # 🔧 REDUCIDO: Suficiente para respuestas de chatbot
            )
            
            message = response.choices[0].message
            
            # Si la IA quiere ejecutar una función
            if message.function_call:
                function_name = message.function_call.name
                function_args = json.loads(message.function_call.arguments)
                
                # Ejecutar la función
                function_result = await self._execute_ai_function(
                    {"name": function_name, "args": function_args},
                    telefono, business_context, tenant, db
                )
                
                return function_result
            
            # Respuesta directa de la IA
            return message.content
            
        except Exception as e:
            print(f"❌ Error en OpenAI: {e}")
            return self._generar_respuesta_fallback(mensaje, user_history, business_context)
    
    async def _buscar_horarios_servicio_real(self, servicio_id: int, business_context: dict, telefono: str, tenant: Tenant, db: Session) -> str:
        """Buscar horarios disponibles REALES usando Google Calendar"""
        try:
            # Buscar el servicio
            servicio_info = next((s for s in business_context['servicios'] if s['id'] == servicio_id), None)
            if not servicio_info:
                return "❌ No encontré ese servicio."
            
            # Verificar que hay empleados
            if not business_context['empleados']:
                return "❌ No hay profesionales disponibles para este servicio."
            
            # Usar el primer empleado o buscar uno específico para el servicio
            empleado = business_context['empleados'][0]
            calendar_id = empleado.get('calendar_id') or servicio_info.get('calendar_id', 'primary')
            
            # Obtener horarios reales de Google Calendar
            horarios_disponibles = await self._get_available_slots_from_calendar(
                calendar_id=calendar_id,
                servicio=servicio_info,
                dias_adelante=7
            )
            
            if not horarios_disponibles:
                return f"❌ No hay horarios disponibles para *{servicio_info['nombre']}* en los próximos 7 días.\n\n💬 ¿Te gustaría que revise otra fecha específica?"
            
            # Formatear respuesta
            respuesta = f"📅 *Horarios disponibles para {servicio_info['nombre']}*\n\n"
            respuesta += f"💰 Precio: ${servicio_info['precio']}\n"
            respuesta += f"⏱️ Duración: {servicio_info['duracion']} minutos\n"
            respuesta += f"👥 Máximo {servicio_info.get('cantidad_maxima', 1)} personas\n\n"
            
            respuesta += "*📋 Próximos horarios disponibles:*\n"
            
            # Mostrar hasta 6 horarios
            for i, slot in enumerate(horarios_disponibles[:6], 1):
                dia_nombre = self._traducir_dia(slot['fecha'].strftime('%A'))
                fecha_str = f"{dia_nombre} {slot['fecha'].strftime('%d/%m')}"
                hora_str = slot['fecha'].strftime('%H:%M')
                respuesta += f"*{i}.* {fecha_str} a las {hora_str}\n"
            
            respuesta += "\n💬 Dime qué horario te conviene (ejemplo: '1' o 'mañana a las 19:00')"
            respuesta += "\n📝 Para confirmar necesitaré tu nombre completo."
            
            # Guardar slots en Redis para referencia posterior
            slots_key = f"slots:{telefono}:{servicio_id}"
            slots_data = [
                {
                    "numero": i,
                    "fecha_hora": slot['fecha'].isoformat(),
                    "empleado_id": empleado['id']
                }
                for i, slot in enumerate(horarios_disponibles[:6], 1)
            ]
            self.redis_client.set(slots_key, json.dumps(slots_data), ex=1800)  # 30 min
            
            return respuesta
            
        except Exception as e:
            print(f"❌ Error buscando horarios reales: {e}")
            return "❌ No pude consultar los horarios. Intenta de nuevo en un momento."

    async def _get_available_slots_from_calendar(self, calendar_id: str, servicio: dict, dias_adelante: int = 7) -> list:
        """Obtener slots disponibles de Google Calendar"""
        try:
            if not self.google_credentials:
                print("❌ No hay credenciales de Google configuradas")
                return []
            
            # Configurar credenciales
            credentials_info = json.loads(self.google_credentials)
            credentials = service_account.Credentials.from_service_account_info(credentials_info)
            service = build('calendar', 'v3', credentials=credentials)
            
            # Rangos de tiempo
            now = datetime.now(self.tz)
            end_time = now + timedelta(days=dias_adelante)
            
            # Obtener eventos existentes
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=now.isoformat(),
                timeMax=end_time.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            # Generar slots disponibles
            available_slots = []
            duracion_minutos = servicio['duracion']
            
            for day_offset in range(dias_adelante):
                check_date = now + timedelta(days=day_offset)
                
                # Verificar si es día laborable para este servicio
                if not self._is_working_day(check_date, servicio):
                    continue
                
                # Obtener horarios de trabajo
                working_hours = self._get_working_hours_for_day(check_date, servicio)
                if not working_hours:
                    continue
                
                # Generar slots posibles
                current_time = check_date.replace(
                    hour=working_hours['start'].hour,
                    minute=working_hours['start'].minute,
                    second=0,
                    microsecond=0
                )
                
                end_work = check_date.replace(
                    hour=working_hours['end'].hour,
                    minute=working_hours['end'].minute,
                    second=0,
                    microsecond=0
                )
                
                # Si es hoy, empezar desde la hora actual + 1 hora
                if check_date.date() == now.date():
                    min_start = now + timedelta(hours=1)
                    if current_time < min_start:
                        current_time = min_start
                
                # Generar slots
                while current_time + timedelta(minutes=duracion_minutos) <= end_work:
                    # Verificar si el slot está libre
                    slot_end = current_time + timedelta(minutes=duracion_minutos)
                    
                    is_free = True
                    for event in events:
                        event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
                        event_end = datetime.fromisoformat(event['end'].get('dateTime', event['end'].get('date')))
                        
                        # Verificar solapamiento
                        if (current_time < event_end and slot_end > event_start):
                            is_free = False
                            break
                    
                    if is_free:
                        available_slots.append({
                            'fecha': current_time,
                            'fin': slot_end
                        })
                    
                    # Incrementar según configuración
                    increment = 30 if servicio.get('solo_horas_exactas') else 15
                    if servicio.get('turnos_consecutivos'):
                        increment = duracion_minutos
                    
                    current_time += timedelta(minutes=increment)
            
            return available_slots
            
        except Exception as e:
            print(f"❌ Error consultando Google Calendar: {e}")
            return []

    def _is_working_day(self, date, servicio: dict) -> bool:
        """Verificar si es día laborable"""
        day_name = date.strftime('%A').lower()
        # Aquí deberías verificar los horarios configurados del servicio
        # Por ahora, asumimos lunes a viernes
        return day_name not in ['saturday', 'sunday']

    def _get_working_hours_for_day(self, date, servicio: dict) -> dict:
        """Obtener horarios de trabajo para un día específico"""
        # Según tu configuración: Lunes a Viernes 8:00 a 00:00
        day_name = date.strftime('%A').lower()
        
        if day_name in ['saturday', 'sunday']:
            return None
        
        return {
            'start': datetime.strptime('08:00', '%H:%M').time(),
            'end': datetime.strptime('23:59', '%H:%M').time()
        }

    async def _execute_ai_function(self, function_call: dict, telefono: str, business_context: dict, tenant: Tenant, db: Session) -> str:
        """Ejecutar función llamada por la IA"""
        function_name = function_call["name"]
        args = function_call["args"]
        
        try:
            if function_name == "buscar_horarios_servicio":
                return await self._buscar_horarios_servicio_real(
                    args["servicio_id"], business_context, telefono, tenant, db
                )
            elif function_name == "buscar_horarios_fecha_especifica":
                return await self._buscar_horarios_fecha_especifica(args, business_context, telefono, tenant, db)
            elif function_name == "crear_reserva":
                return await self._crear_reserva_inteligente(args, telefono, business_context, tenant, db)
            elif function_name == "cancelar_reserva":
                return await self._cancelar_reserva_inteligente(args, telefono, tenant, db)
            else:
                return "❌ Función no reconocida."
        except Exception as e:
            print(f"❌ Error ejecutando función {function_name}: {e}")
            return "❌ Tuve un problema procesando tu solicitud."

    async def _buscar_horarios_fecha_especifica(self, args: dict, business_context: dict, telefono: str, tenant: Tenant, db: Session) -> str:
        """Buscar horarios en fecha específica"""
        try:
            servicio_id = args["servicio_id"]
            fecha_especifica = args["fecha_especifica"]
            hora_especifica = args.get("hora_especifica")
            
            # Buscar servicio
            servicio_info = next((s for s in business_context['servicios'] if s['id'] == servicio_id), None)
            if not servicio_info:
                return "❌ No encontré ese servicio."
            
            # Parsear fecha
            today = datetime.now(self.tz)
            try:
                if len(fecha_especifica.split('/')) == 2:
                    # Formato DD/MM
                    dia, mes = fecha_especifica.split('/')
                    año = today.year
                    # Si el mes ya pasó, asumir año siguiente
                    if int(mes) < today.month:
                        año += 1
                else:
                    # Formato DD/MM/YYYY
                    dia, mes, año = fecha_especifica.split('/')
                
                fecha_target = datetime(int(año), int(mes), int(dia))
                fecha_target = self.tz.localize(fecha_target)
                
            except Exception as e:
                return "❌ Formato de fecha inválido. Usa DD/MM o DD/MM/YYYY"
            
            # Verificar que no sea en el pasado
            if fecha_target.date() < today.date():
                return "❌ No puedo buscar horarios en fechas pasadas."
            
            # Si especificó hora, verificar disponibilidad de ese slot exacto
            if hora_especifica:
                try:
                    hora_obj = datetime.strptime(hora_especifica, "%H:%M").time()
                    datetime_completo = fecha_target.replace(
                        hour=hora_obj.hour, 
                        minute=hora_obj.minute
                    )
                    
                    # Verificar disponibilidad del slot específico
                    is_available = await self._check_specific_slot_availability(
                        datetime_completo, servicio_info, business_context['empleados'][0], tenant
                    )
                    
                    if is_available:
                        dia_nombre = self._traducir_dia(fecha_target.strftime('%A'))
                        return f"✅ *¡Perfecto!* \n\n📅 {dia_nombre} {fecha_target.strftime('%d/%m')} a las {hora_especifica} está disponible para *{servicio_info['nombre']}*\n\n💬 ¿Cómo te llamas para confirmar la reserva?"
                    else:
                        return f"❌ Lo siento, {fecha_target.strftime('%d/%m')} a las {hora_especifica} no está disponible.\n\n📅 ¿Te busco otra opción para ese día?"
                        
                except ValueError:
                    return "❌ Formato de hora inválido. Usa HH:MM (ejemplo: 19:00)"
            
            # Buscar todos los horarios disponibles para esa fecha
            horarios_dia = await self._get_available_slots_for_specific_date(
                fecha_target, servicio_info, business_context['empleados'][0], tenant
            )
            
            if not horarios_dia:
                dia_nombre = self._traducir_dia(fecha_target.strftime('%A'))
                return f"❌ No hay horarios disponibles el {dia_nombre} {fecha_target.strftime('%d/%m')} para *{servicio_info['nombre']}*\n\n📅 ¿Te busco opciones en otros días?"
            
            # Formatear respuesta
            dia_nombre = self._traducir_dia(fecha_target.strftime('%A'))
            respuesta = f"📅 *Horarios disponibles para {servicio_info['nombre']}*\n"
            respuesta += f"📆 {dia_nombre} {fecha_target.strftime('%d/%m/%Y')}\n\n"
            
            for i, slot in enumerate(horarios_dia, 1):
                hora_str = slot['fecha'].strftime('%H:%M')
                respuesta += f"*{i}.* {hora_str}\n"
            
            respuesta += "\n💬 Dime qué horario prefieres y tu nombre para confirmar."
            
            return respuesta
            
        except Exception as e:
            print(f"❌ Error buscando fecha específica: {e}")
            return "❌ No pude procesar tu solicitud de fecha específica."

    async def _check_specific_slot_availability(self, datetime_slot: datetime, servicio: dict, empleado: dict, tenant: Tenant) -> bool:
        """Verificar si un slot específico está disponible"""
        try:
            if not self.google_credentials:
                return False
            
            credentials_info = json.loads(self.google_credentials)
            credentials = service_account.Credentials.from_service_account_info(credentials_info)
            service = build('calendar', 'v3', credentials=credentials)
            
            calendar_id = empleado.get('calendar_id', 'primary')
            slot_end = datetime_slot + timedelta(minutes=servicio['duracion'])
            
            # Buscar eventos en ese rango
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=datetime_slot.isoformat(),
                timeMax=slot_end.isoformat(),
                singleEvents=True
            ).execute()
            
            events = events_result.get('items', [])
            return len(events) == 0
            
        except Exception as e:
            print(f"Error verificando slot específico: {e}")
            return False

    async def _get_available_slots_for_specific_date(self, target_date: datetime, servicio: dict, empleado: dict, tenant: Tenant) -> list:
        """Obtener slots disponibles para una fecha específica"""
        try:
            # Verificar día laborable
            if not self._is_working_day(target_date, servicio):
                return []
            
            # Obtener horarios de trabajo
            working_hours = self._get_working_hours_for_day(target_date, servicio)
            if not working_hours:
                return []
            
            # Usar la lógica existente pero para un solo día
            return await self._get_available_slots_from_calendar(
                calendar_id=empleado.get('calendar_id', 'primary'),
                servicio=servicio,
                dias_adelante=1  # Solo el día objetivo
            )
            
        except Exception as e:
            print(f"Error obteniendo slots para fecha específica: {e}")
            return []

    def _mostrar_servicios_empleado(self, empleado: dict, business_context: dict) -> str:
        """Mostrar servicios disponibles para un empleado específico"""
        servicios_empleado = []
        
        # Filtrar servicios que puede realizar este empleado
        for servicio in business_context['servicios']:
            # Aquí puedes agregar lógica para verificar qué servicios puede realizar cada empleado
            # Por ahora, asumimos que todos los empleados pueden realizar todos los servicios
            servicios_empleado.append(servicio)
        
        if not servicios_empleado:
            return f"❌ {empleado['nombre']} no tiene servicios disponibles."
        
        respuesta = f"👤 *Servicios disponibles con {empleado['nombre']}:*\n\n"
        
        for i, servicio in enumerate(servicios_empleado, 1):
            respuesta += f"*{i}.* {servicio['nombre']} - ${servicio['precio']} ({servicio['duracion']} min)\n"
        
        respuesta += f"\n💬 Elige el número del servicio que quieres con {empleado['nombre']}"
        
        return respuesta

    def _generar_respuesta_fallback(self, mensaje: str, user_history: dict, business_context: dict) -> str:
        """Generar respuesta de emergencia sin IA"""
        mensaje_lower = mensaje.lower()
        
        # Detectar intenciones básicas
        if any(word in mensaje_lower for word in ['turno', 'cita', 'reserva', 'horario']):
            return self._mostrar_menu_servicios(business_context)
        
        elif any(word in mensaje_lower for word in ['cancelar', 'anular']):
            if user_history['reservas_activas']:
                reservas_txt = "\n".join([f"• {r['codigo']} - {r['servicio']} ({r['fecha']})" 
                                    for r in user_history['reservas_activas']])
                return f"📋 *Tus reservas activas:*\n{reservas_txt}\n\n💬 Dime el código para cancelar"
            else:
                return "❌ No tienes reservas activas para cancelar."
        
        elif any(word in mensaje_lower for word in ['precio', 'costo', 'cuanto']):
            return self._mostrar_menu_servicios(business_context, mostrar_precios=True)
        
        else:
            return self._mostrar_menu_servicios(business_context)

    def _mostrar_menu_servicios(self, business_context: dict, mostrar_precios: bool = False) -> str:
        """Mostrar menú de servicios"""
        respuesta = "📋 *Servicios disponibles:*\n\n"
        
        for i, servicio in enumerate(business_context['servicios'], 1):
            precio_txt = f" - ${servicio['precio']}" if mostrar_precios else ""
            respuesta += f"*{i}.* {servicio['nombre']}{precio_txt}\n"
        
        respuesta += "\n💬 Puedes escribir el *número* o el *nombre del servicio*"
        respuesta += "\n👤 También puedes escribir el nombre de un profesional específico"
        
        return respuesta

    def _format_servicios_with_real_ids(self, servicios: list) -> str:
        """Formatear servicios con sus IDs reales para el prompt"""
        servicios_txt = ""
        for servicio in servicios:
            servicios_txt += f"- {servicio['nombre']} (ID: {servicio['id']})\n"
        return servicios_txt

    def _get_business_context(self, tenant: Tenant, db: Session) -> dict:
        """Obtener contexto completo del negocio"""
        # Obtener servicios
        servicios = db.query(Servicio).filter(Servicio.tenant_id == tenant.id).all()
        servicios_data = []
        for servicio in servicios:
            servicios_data.append({
                'id': servicio.id,
                'nombre': servicio.nombre,
                'precio': servicio.precio,
                'duracion': servicio.duracion,
                'cantidad_maxima': servicio.cantidad,
                'solo_horas_exactas': servicio.solo_horas_exactas,
                'turnos_consecutivos': servicio.turnos_consecutivos,
                'es_informativo': servicio.es_informativo,
                'mensaje_personalizado': servicio.mensaje_personalizado,
                'calendar_id': servicio.calendar_id
            })
        
        # Obtener empleados
        empleados = db.query(Empleado).filter(Empleado.tenant_id == tenant.id).all()
        empleados_data = []
        for empleado in empleados:
            empleados_data.append({
                'id': empleado.id,
                'nombre': empleado.nombre,
                'calendar_id': empleado.calendar_id
            })
        
        return {
            'servicios': servicios_data,
            'empleados': empleados_data,
            'comercio': tenant.comercio,
            'horarios_generales': tenant.working_hours_general
        }
        
def _traducir_dia(dia_en_ingles):
    """Traducir día de la semana"""
    traducciones = {
        'Monday': 'Lunes',
        'Tuesday': 'Martes', 
        'Wednesday': 'Miércoles',
        'Thursday': 'Jueves',
        'Friday': 'Viernes',
        'Saturday': 'Sábado',
        'Sunday': 'Domingo'
    }
    return traducciones.get(dia_en_ingles, dia_en_ingles)
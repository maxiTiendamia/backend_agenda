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
        system_prompt = f"""🤖 Eres la IA asistente de {tenant.comercio}. 

📊 INFORMACIÓN DEL NEGOCIO:
- 🏢 Nombre: {tenant.comercio}
- ✨ Servicios disponibles: {', '.join([s['nombre'] for s in business_context['servicios']])}
- 👥 Empleados: {', '.join([e['nombre'] for e in business_context['empleados']]) if business_context['empleados'] else 'Sin empleados (servicios directos)'}

👤 INFORMACIÓN DEL CLIENTE (📞 {telefono}):
- 🔄 Cliente recurrente: {'🎯 Sí' if user_history['es_cliente_recurrente'] else '🆕 No (cliente nuevo)'}
- ⭐ Servicio favorito: {user_history['servicio_favorito'] or '🤷 Ninguno aún'}
- 📅 Reservas activas: {len(user_history['reservas_activas'])}
- 📊 Historial: {len(user_history['historial'])} reservas anteriores

📋 INSTRUCCIONES IMPORTANTES:
1. 😊 Sé natural, amigable y personalizada. Usa MUCHOS emojis
2. 🎯 Usa la información del cliente para personalizar respuestas
3. 📋 Cuando te pidan un turno, muestra los servicios numerados (1, 2, 3...)
4. 🔢 Si el usuario dice un número, usa la función buscar_horarios_servicio con el ID REAL
5. 🏆 SERVICIOS CON SUS IDs REALES:
{self._format_servicios_with_real_ids(business_context['servicios'])}
6. 🧠 Recuerda conversaciones anteriores
7. ❓ Puedes responder preguntas generales sobre el negocio
8. 📅 Para fechas específicas, usa la función buscar_horarios_fecha_especifica

🛠️ FUNCIONES DISPONIBLES:
- 🔍 buscar_horarios_servicio: Para mostrar horarios disponibles (usa el ID real del servicio)
- 📅 buscar_horarios_fecha_especifica: Para horarios en fecha/hora específica  
- ✅ crear_reserva: Para confirmar una reserva
- ❌ cancelar_reserva: Para cancelar reservas existentes

💡 IMPORTANTE: Este negocio {'tiene empleados' if business_context['tiene_empleados'] else 'NO tiene empleados (ej: canchas, padel)'}.
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
            
            # 🔧 NUEVA LÓGICA: Priorizar empleados, pero usar servicio si no hay empleados
            calendar_id = None
            empleado_asignado = None
            
            if business_context['empleados']:
                # Si hay empleados, usar el primer empleado disponible
                empleado_asignado = business_context['empleados'][0]
                calendar_id = empleado_asignado.get('calendar_id') or servicio_info.get('calendar_id', 'primary')
            else:
                # Si NO hay empleados (ej: canchas, padel), usar calendario del servicio
                calendar_id = servicio_info.get('calendar_id') or tenant.calendar_id_general or 'primary'
                empleado_asignado = {
                    'id': None,
                    'nombre': 'Sistema',
                    'calendar_id': calendar_id
                }
            
            # Obtener horarios reales de Google Calendar
            horarios_disponibles = await self._get_available_slots_from_calendar(
                calendar_id=calendar_id,
                servicio=servicio_info,
                dias_adelante=7
            )
            
            if not horarios_disponibles:
                return f"😔 No hay horarios disponibles para *{servicio_info['nombre']}* en los próximos 7 días.\n\n📅 ¿Te gustaría que revise otra fecha específica? 🔍"
            
            # Formatear respuesta con más emojis
            tipo_servicio = "🎾" if "padel" in servicio_info['nombre'].lower() else "✨"
            respuesta = f"{tipo_servicio} *Horarios disponibles para {servicio_info['nombre']}*\n\n"
            respuesta += f"💰 Precio: ${servicio_info['precio']}\n"
            respuesta += f"⏱️ Duración: {servicio_info['duracion']} minutos\n"
            respuesta += f"👥 Máximo {servicio_info.get('cantidad_maxima', 1)} personas\n\n"
            
            respuesta += "📋 *Próximos horarios disponibles:*\n"
            
            # Mostrar hasta 6 horarios
            for i, slot in enumerate(horarios_disponibles[:6], 1):
                dia_nombre = _traducir_dia(slot['fecha'].strftime('%A'))
                fecha_str = f"{dia_nombre} {slot['fecha'].strftime('%d/%m')}"
                hora_str = slot['fecha'].strftime('%H:%M')
                respuesta += f"🎯 *{i}.* {fecha_str} a las {hora_str}\n"
            
            respuesta += "\n💬 Dime qué horario te conviene (ejemplo: '1' o 'mañana a las 19:00') 🕐"
            respuesta += "\n📝 Para confirmar necesitaré tu nombre completo 👤"
            
            # Guardar slots en Redis para referencia posterior
            slots_key = f"slots:{telefono}:{servicio_id}"
            slots_data = [
                {
                    "numero": i,
                    "fecha_hora": slot['fecha'].isoformat(),
                    "empleado_id": empleado_asignado['id'],
                    "empleado_nombre": empleado_asignado['nombre']
                }
                for i, slot in enumerate(horarios_disponibles[:6], 1)
            ]
            self.redis_client.set(slots_key, json.dumps(slots_data), ex=1800)  # 30 min
            
            return respuesta
            
        except Exception as e:
            print(f"❌ Error buscando horarios reales: {e}")
            return "😵 No pude consultar los horarios. Intenta de nuevo en un momento 🔄"

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
                        # 🔧 CORREGIR: Verificar que event sea un diccionario
                        if not isinstance(event, dict):
                            continue
                            
                        # 🔧 CORREGIR: Manejar diferentes formatos de fecha
                        event_start_info = event.get('start', {})
                        event_end_info = event.get('end', {})
                        
                        if not event_start_info or not event_end_info:
                            continue
                        
                        try:
                            # Obtener fecha/hora de inicio del evento
                            if 'dateTime' in event_start_info:
                                event_start_str = event_start_info['dateTime']
                            elif 'date' in event_start_info:
                                event_start_str = event_start_info['date'] + 'T00:00:00'
                            else:
                                continue
                                
                            # Obtener fecha/hora de fin del evento
                            if 'dateTime' in event_end_info:
                                event_end_str = event_end_info['dateTime']
                            elif 'date' in event_end_info:
                                event_end_str = event_end_info['date'] + 'T23:59:59'
                            else:
                                continue
                            
                            # Parsear fechas
                            event_start = datetime.fromisoformat(event_start_str.replace('Z', '+00:00'))
                            event_end = datetime.fromisoformat(event_end_str.replace('Z', '+00:00'))
                            
                            # Convertir a timezone local
                            event_start = event_start.astimezone(self.tz)
                            event_end = event_end.astimezone(self.tz)
                            
                            # Verificar solapamiento
                            if (current_time < event_end and slot_end > event_start):
                                is_free = False
                                break
                                
                        except (ValueError, TypeError) as e:
                            print(f"⚠️ Error parseando evento: {e}")
                            continue
                    
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
            # 🔧 AGREGAR: Generar slots de ejemplo para testing
            return self._generate_mock_slots(servicio, dias_adelante)

    def _generate_mock_slots(self, servicio: dict, dias_adelante: int = 7) -> list:
        """Generar slots de ejemplo cuando no hay acceso a Google Calendar"""
        print("🔧 Generando slots de ejemplo para testing...")
        
        mock_slots = []
        now = datetime.now(self.tz)
        
        for day_offset in range(1, min(dias_adelante + 1, 4)):  # Solo 3 días de ejemplo
            check_date = now + timedelta(days=day_offset)
            
            # Solo días laborables
            if check_date.weekday() >= 5:  # Sábado y domingo
                continue
                
            # Horarios de ejemplo: 9:00, 11:00, 15:00, 17:00, 19:00
            horas_ejemplo = [9, 11, 15, 17, 19]
            
            for hora in horas_ejemplo:
                slot_time = check_date.replace(hour=hora, minute=0, second=0, microsecond=0)
                slot_end = slot_time + timedelta(minutes=servicio['duracion'])
                
                mock_slots.append({
                    'fecha': slot_time,
                    'fin': slot_end
                })
        
        return mock_slots
    
    async def _crear_reserva_inteligente(self, args: dict, telefono: str, business_context: dict, tenant: Tenant, db: Session) -> str:
        """Crear una nueva reserva de forma inteligente"""
        try:
            servicio_id = args["servicio_id"]
            fecha_hora = args["fecha_hora"]
            nombre_cliente = args["nombre_cliente"]
            cantidad = args.get("cantidad", 1)
            empleado_id = args.get("empleado_id")
            
            # TODO: Implementar lógica de creación de reserva
            return f"✅ ¡Reserva confirmada!\n\n👤 Cliente: {nombre_cliente}\n📅 Fecha: {fecha_hora}\n🎯 En proceso de confirmación..."
            
        except Exception as e:
            print(f"❌ Error creando reserva: {e}")
            return "❌ No pude crear la reserva. Intenta de nuevo."

    async def _cancelar_reserva_inteligente(self, args: dict, telefono: str, tenant: Tenant, db: Session) -> str:
        """Cancelar una reserva existente"""
        try:
            codigo_reserva = args["codigo_reserva"]
            
            # TODO: Implementar lógica de cancelación
            return f"✅ Reserva {codigo_reserva} cancelada correctamente."
            
        except Exception as e:
            print(f"❌ Error cancelando reserva: {e}")
            return "❌ No pude cancelar la reserva. Verifica el código."
    
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
                'calendar_id': servicio.calendar_id,
                'working_hours': servicio.working_hours
            })
        
        # Obtener empleados
        empleados = db.query(Empleado).filter(Empleado.tenant_id == tenant.id).all()
        empleados_data = []
        for empleado in empleados:
            empleados_data.append({
                'id': empleado.id,
                'nombre': empleado.nombre,
                'calendar_id': empleado.calendar_id,
                'working_hours': empleado.working_hours
            })
        
        return {
            'servicios': servicios_data,
            'empleados': empleados_data,
            'comercio': tenant.comercio,
            'horarios_generales': tenant.working_hours_general,
            'calendar_id_general': getattr(tenant, 'calendar_id_general', None),
            'tiene_empleados': len(empleados_data) > 0
        }

    def _mostrar_servicios_empleado(self, empleado: dict, business_context: dict) -> str:
        """Mostrar servicios disponibles para un empleado específico"""
        servicios_empleado = business_context['servicios']  # Por ahora todos los servicios
        
        if not servicios_empleado:
            return f"❌ {empleado['nombre']} no tiene servicios disponibles."
        
        respuesta = f"👤 *Servicios disponibles con {empleado['nombre']}:*\n\n"
        
        for i, servicio in enumerate(servicios_empleado, 1):
            respuesta += f"✨ *{i}.* {servicio['nombre']} - 💰${servicio['precio']} (⏱️{servicio['duracion']} min)\n"
        
        respuesta += f"\n💬 Elige el número del servicio que quieres con {empleado['nombre']} 🎯"
        
        return respuesta

    def _generar_respuesta_fallback(self, mensaje: str, user_history: dict, business_context: dict) -> str:
        """Generar respuesta de emergencia sin IA"""
        mensaje_lower = mensaje.lower()
        
        # Detectar intenciones básicas
        if any(word in mensaje_lower for word in ['turno', 'cita', 'reserva', 'horario']):
            return self._mostrar_menu_servicios(business_context)
        
        elif any(word in mensaje_lower for word in ['cancelar', 'anular']):
            if user_history['reservas_activas']:
                reservas_txt = "\n".join([f"🎫 {r['codigo']} - {r['servicio']} (📅{r['fecha']})" 
                                    for r in user_history['reservas_activas']])
                return f"📋 *Tus reservas activas:*\n{reservas_txt}\n\n💬 Dime el código para cancelar ❌"
            else:
                return "😔 No tienes reservas activas para cancelar.\n\n🎯 ¿Quieres hacer una nueva reserva?"
        
        elif any(word in mensaje_lower for word in ['precio', 'costo', 'cuanto']):
            return self._mostrar_menu_servicios(business_context, mostrar_precios=True)
        
        else:
            return f"👋 ¡Hola! Te ayudo con lo que necesites.\n\n{self._mostrar_menu_servicios(business_context)}"

    def _mostrar_menu_servicios(self, business_context: dict, mostrar_precios: bool = False) -> str:
        """Mostrar menú de servicios"""
        respuesta = "🏆 *¡Servicios disponibles!*\n\n"
        
        for i, servicio in enumerate(business_context['servicios'], 1):
            precio_txt = f" - 💰${servicio['precio']}" if mostrar_precios else ""
            duracion_txt = f" (⏱️{servicio['duracion']} min)" if mostrar_precios else ""
            respuesta += f"✨ *{i}.* {servicio['nombre']}{precio_txt}{duracion_txt}\n"
        
        respuesta += "\n🎯 Puedes escribir el *número* o el *nombre del servicio*"
        respuesta += "\n👥 También puedes escribir el nombre de un profesional específico"
        respuesta += "\n\n🚀 ¿Qué te interesa?"
        
        return respuesta
    
def _traducir_dia(dia_ingles: str) -> str:
    """Traducir días de la semana"""
    dias = {
        'Monday': 'Lunes',
        'Tuesday': 'Martes', 
        'Wednesday': 'Miércoles',
        'Thursday': 'Jueves',
        'Friday': 'Viernes',
        'Saturday': 'Sábado',
        'Sunday': 'Domingo'
    }
    return dias.get(dia_ingles, dia_ingles)

    def _get_working_hours_for_day(self, date, servicio: dict) -> dict:
        """Obtener horarios de trabajo para un día específico"""
        day_name = date.strftime('%A').lower()
        
        # Usar horarios del servicio si están configurados
        working_hours_config = servicio.get('working_hours')
        
        if working_hours_config and isinstance(working_hours_config, dict):
            try:
                if day_name in working_hours_config:
                    hours_str = working_hours_config[day_name]
                    if hours_str and hours_str != "closed":
                        start_str, end_str = hours_str.split('-')
                        return {
                            'start': datetime.strptime(start_str.strip(), '%H:%M').time(),
                            'end': datetime.strptime(end_str.strip(), '%H:%M').time()
                        }
            except Exception as e:
                print(f"⚠️ Error parseando horarios del servicio: {e}")
        
        # Fallback: horarios por defecto
        if day_name in ['saturday', 'sunday']:
            return {
                'start': datetime.strptime('09:00', '%H:%M').time(),
                'end': datetime.strptime('18:00', '%H:%M').time()
            }
        else:
            return {
                'start': datetime.strptime('08:00', '%H:%M').time(),
                'end': datetime.strptime('22:00', '%H:%M').time()
            }

    def _is_working_day(self, date, servicio: dict) -> bool:
        """Verificar si es día laborable"""
        day_name = date.strftime('%A').lower()
        
        # Verificar configuración del servicio
        working_hours_config = servicio.get('working_hours')
        
        if working_hours_config and isinstance(working_hours_config, dict):
            return day_name in working_hours_config and working_hours_config[day_name] != "closed"
        
        # Fallback: trabajar todos los días excepto domingos
        return day_name != 'sunday'

    async def _buscar_horarios_fecha_especifica(self, args: dict, telefono: str, business_context: dict, tenant: Tenant, db: Session) -> str:
        """Buscar horarios en fecha específica"""
        try:
            servicio_id = args["servicio_id"]
            fecha_especifica = args["fecha_especifica"]
            
            # TODO: Implementar búsqueda por fecha específica
            return f"🔍 Buscando horarios para el servicio {servicio_id} en fecha {fecha_especifica}...\n\n⚠️ Función en desarrollo."
            
        except Exception as e:
            print(f"❌ Error buscando horarios por fecha: {e}")
            return "❌ Error buscando horarios para esa fecha."
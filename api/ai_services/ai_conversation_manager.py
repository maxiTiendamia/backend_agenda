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

            # --- FLUJO DE CANCELACIÓN ---
            mensaje_stripped = mensaje.strip().lower()
            if "cancelar" in mensaje_stripped or "anular" in mensaje_stripped:
                codigo_match = re.search(r'\b([A-Z0-9]{6,})\b', mensaje)
                if codigo_match:
                    codigo_reserva = codigo_match.group(1)
                    return await self.cancelar_reserva(codigo_reserva, telefono, db)
                else:
                    reservas_activas = user_history.get("reservas_activas", [])
                    if not reservas_activas:
                        return "No tienes reservas activas para cancelar."
                    respuesta = "🔄 Tus reservas activas:\n"
                    for r in reservas_activas:
                        respuesta += f"- Código: {r['codigo']} | {r['servicio']} el {r['fecha']}\n"
                    respuesta += "\n💬 Escribe el código de la reserva que deseas cancelar."
                    return respuesta

            # --- FLUJO DE CONSULTA DE SERVICIOS ---
            if mensaje_stripped in ["servicios", "ver servicios", "lista", "menu"]:
                return self.mostrar_servicios(business_context)

            # --- FLUJO PRINCIPAL CON IA ---
            respuesta = await self._ai_process_conversation_natural(
                mensaje, telefono, conversation_history, user_history, business_context, tenant, db
            )
            self._save_conversation_message(telefono, "assistant", respuesta)
            return respuesta

        except Exception as e:
            print(f"❌ Error en AI manager: {e}")
            return "Disculpa, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?"

    def _detectar_dia_mensaje(self, mensaje: str) -> str:
        """🔧 CORREGIDO: Detectar qué día quiere el usuario"""
        mensaje = mensaje.lower()
        
        if any(word in mensaje for word in ['hoy', 'today']):
            return 'hoy'
        elif any(word in mensaje for word in ['mañana', 'tomorrow']):
            return 'mañana'
        elif any(word in mensaje for word in ['lunes', 'monday']):
            return 'lunes'
        elif any(word in mensaje for word in ['martes', 'tuesday']):
            return 'martes'
        elif any(word in mensaje for word in ['miércoles', 'miercoles', 'wednesday']):
            return 'miercoles'
        elif any(word in mensaje for word in ['jueves', 'thursday']):
            return 'jueves'
        elif any(word in mensaje for word in ['viernes', 'friday']):
            return 'viernes'
        elif any(word in mensaje for word in ['sábado', 'sabado', 'saturday']):
            return 'sabado'
        elif any(word in mensaje for word in ['domingo', 'sunday']):
            return 'domingo'
        
        return None

    async def _buscar_horarios_dia_especifico(self, servicio: dict, dia: str, telefono: str, business_context: dict, tenant: Tenant, db: Session) -> str:
        """🔧 CORREGIDO: Buscar horarios para un día específico"""
        try:
            # Limpiar selección de servicio
            servicio_key = f"servicio_seleccionado:{telefono}"
            self.redis_client.delete(servicio_key)
            
            # Calcular fecha objetivo
            now = datetime.now(self.tz)
            
            if dia == 'hoy':
                fecha_objetivo = now
            elif dia == 'mañana':
                fecha_objetivo = now + timedelta(days=1)
            else:
                # Encontrar el próximo día de la semana
                dias_semana = {
                    'lunes': 0, 'martes': 1, 'miercoles': 2, 'jueves': 3, 
                    'viernes': 4, 'sabado': 5, 'domingo': 6
                }
                
                if dia in dias_semana:
                    dias_hasta = (dias_semana[dia] - now.weekday()) % 7
                    if dias_hasta == 0:  # Es hoy
                        dias_hasta = 7  # Próxima semana
                    fecha_objetivo = now + timedelta(days=dias_hasta)
                else:
                    return "❌ No entendí qué día querés. Intentá de nuevo."
            
            print(f"🔧 DEBUG: Buscando horarios para {servicio['nombre']} el {fecha_objetivo.strftime('%d/%m/%Y')}")
            
            # Obtener horarios disponibles para ese día específico
            calendar_id = servicio.get('calendar_id') or business_context.get('calendar_id_general', 'primary')
            
            # 🔧 USAR MÉTODO CORREGIDO
            horarios_disponibles = await self._get_available_slots_for_specific_day(
                calendar_id=calendar_id,
                servicio=servicio,
                fecha_objetivo=fecha_objetivo
            )
            
            if not horarios_disponibles:
                dia_nombre = fecha_objetivo.strftime('%A')
                dia_traducido = _traducir_dia(dia_nombre)
                return f"😔 No hay horarios disponibles para *{servicio['nombre']}* el {dia_traducido} {fecha_objetivo.strftime('%d/%m')}.\n\n📅 ¿Te gustaría elegir otro día? 🔄"
            
            # Formatear respuesta
            tipo_servicio = "🎾" if "padel" in servicio['nombre'].lower() else "✨"
            dia_nombre = _traducir_dia(fecha_objetivo.strftime('%A'))
            
            respuesta = f"{tipo_servicio} *Horarios para {servicio['nombre']}*\n"
            respuesta += f"📅 {dia_nombre} {fecha_objetivo.strftime('%d/%m/%Y')}\n\n"
            
            # Mostrar horarios
            for i, slot in enumerate(horarios_disponibles[:8], 1):  # Hasta 8 horarios
                hora_str = slot['fecha'].strftime('%H:%M')
                respuesta += f"🎯 *{i}.* {hora_str}\n"
            
            respuesta += "\n💬 Dime qué horario te conviene (ejemplo: '1' o '19:00') 🕐"
            respuesta += "\n📝 Para confirmar necesitaré tu nombre completo 👤"
            
            # Guardar slots en Redis
            slots_key = f"slots:{telefono}:{servicio['id']}"
            slots_data = [
                {
                    "numero": i,
                    "fecha_hora": slot['fecha'].isoformat(),
                    "empleado_id": None,
                    "empleado_nombre": "Sistema"
                }
                for i, slot in enumerate(horarios_disponibles[:8], 1)
            ]
            self.redis_client.set(slots_key, json.dumps(slots_data), ex=1800)
            
            return respuesta
            
        except Exception as e:
            print(f"❌ Error buscando horarios específicos: {e}")
            return "😵 No pude consultar los horarios. Intenta de nuevo 🔄"

    async def _get_available_slots_for_specific_day(self, calendar_id: str, servicio: dict, fecha_objetivo: datetime) -> list:
        """🔧 NUEVA FUNCIÓN: Obtener slots para un día específico"""
        try:
            # Usar el método existente pero filtrar solo para ese día
            slots_todos = await self._get_available_slots_from_calendar(
                calendar_id=calendar_id,
                servicio=servicio,
                dias_adelante=1 if fecha_objetivo.date() == datetime.now(self.tz).date() else 2
            )
            
            # Filtrar solo slots del día objetivo
            slots_dia = []
            for slot in slots_todos:
                if slot['fecha'].date() == fecha_objetivo.date():
                    slots_dia.append(slot)
            
            return slots_dia
            
        except Exception as e:
            print(f"❌ Error obteniendo slots específicos: {e}")
            return []
    
    async def _ai_process_conversation_natural(self, mensaje, telefono, conversation_history, user_history, business_context, tenant, db):
        """🔧 CORREGIDO: Procesamiento de IA más natural y contextual"""
        
        mensaje_stripped = mensaje.strip().lower()
        
        # 🔧 VERIFICAR PRIMERO SI TIENE SERVICIO SELECCIONADO Y HORARIOS DISPONIBLES
        servicio_key = f"servicio_seleccionado:{telefono}"
        servicio_guardado_str = self.redis_client.get(servicio_key)
        
        if servicio_guardado_str:
            servicio_guardado = json.loads(servicio_guardado_str)
            
            # Verificar si hay slots disponibles guardados
            slots_key = f"slots:{telefono}:{servicio_guardado['id']}"
            slots_data_str = self.redis_client.get(slots_key)
            
            if slots_data_str:
                # El usuario está en modo selección de horario
                slots_data = json.loads(slots_data_str)
                
                # Verificar si es selección de número de slot
                if mensaje_stripped.isdigit():
                    try:
                        slot_numero = int(mensaje_stripped)
                        if 1 <= slot_numero <= len(slots_data):
                            slot_seleccionado = slots_data[slot_numero - 1]
                            
                            # Pedir nombre del cliente para confirmar
                            return f"✅ Perfecto! Elegiste:\n\n🎾 *{servicio_guardado['nombre']}*\n📅 {datetime.fromisoformat(slot_seleccionado['fecha_hora']).strftime('%A %d/%m a las %H:%M')}\n\n👤 Para confirmar, necesito tu *nombre completo* por favor:"
                        else:
                            return f"❌ Por favor elige un número entre 1 y {len(slots_data)}"
                    except:
                        pass
                
                # Verificar si es selección por hora (ej: "19:00")
                import re
                time_pattern = r'\b(\d{1,2}):(\d{2})\b'
                time_match = re.search(time_pattern, mensaje_stripped)
                if time_match:
                    hora_buscada = f"{time_match.group(1).zfill(2)}:{time_match.group(2)}"
                    for slot in slots_data:
                        slot_hora = datetime.fromisoformat(slot['fecha_hora']).strftime('%H:%M')
                        if slot_hora == hora_buscada:
                            return f"✅ Perfecto! Elegiste:\n\n🎾 *{servicio_guardado['nombre']}*\n📅 {datetime.fromisoformat(slot['fecha_hora']).strftime('%A %d/%m a las %H:%M')}\n\n👤 Para confirmar, necesito tu *nombre completo* por favor:"
                    
                    return f"❌ No encontré el horario {hora_buscada}. Los horarios disponibles son los numerados arriba."
            
            # Si tiene servicio pero no slots, está eligiendo día
            dia_detectado = self._detectar_dia_mensaje(mensaje_stripped)
            if dia_detectado:
                print(f"🔧 DEBUG: Día detectado: {dia_detectado}")
                return await self._buscar_horarios_dia_especifico(
                    servicio_guardado, dia_detectado, telefono, business_context, tenant, db
                )
        
        # 🔧 DETECCIÓN DE SELECCIÓN DE SERVICIO (solo si NO tiene servicio guardado)
        servicio_seleccionado = None
        
        print(f"🔧 DEBUG: Mensaje recibido: '{mensaje}' - Servicios disponibles: {[s['nombre'] for s in business_context['servicios']]}")
        
        # Verificar si es un número
        if mensaje_stripped.isdigit():
            try:
                posicion = int(mensaje_stripped)
                if 1 <= posicion <= len(business_context['servicios']):
                    servicio_seleccionado = business_context['servicios'][posicion - 1]
                    print(f"🔧 DEBUG: Servicio seleccionado por número {posicion}: {servicio_seleccionado['nombre']} (ID: {servicio_seleccionado['id']})")
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
                    print(f"🔧 DEBUG: Servicio seleccionado por nombre: {servicio_seleccionado['nombre']} (ID: {servicio_seleccionado['id']})")
                    break

        # Si encontró un servicio
        if servicio_seleccionado:
            # 🔧 VERIFICAR SI ES INFORMATIVO
            es_informativo = servicio_seleccionado.get('es_informativo', False)
            print(f"🔧 DEBUG: Servicio {servicio_seleccionado['nombre']} - Es informativo: {es_informativo}")
            
            if es_informativo:
                mensaje_personalizado = servicio_seleccionado.get('mensaje_personalizado', '')
                if mensaje_personalizado:
                    return f"ℹ️ *{servicio_seleccionado['nombre']}*\n\n{mensaje_personalizado}\n\n💬 ¿Necesitas más información? 🤔"
                else:
                    return f"ℹ️ *{servicio_seleccionado['nombre']}*\n\nEste es un servicio informativo.\n\n💬 ¿En qué más puedo ayudarte? 🤔"
            
            # 🔧 GUARDAR SERVICIO SELECCIONADO Y PREGUNTAR DÍA
            servicio_key = f"servicio_seleccionado:{telefono}"
            self.redis_client.set(servicio_key, json.dumps(servicio_seleccionado), ex=1800)  # 30 min
            
            return self._preguntar_dia_disponible(servicio_seleccionado, telefono)
        
        # 🔧 RESTO DEL PROCESAMIENTO CON IA
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

🛠️ FUNCIONES DISPONIBLES:
- 🔍 buscar_horarios_servicio: Para mostrar horarios disponibles (usa el ID real del servicio)
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
                model="gpt-3.5-turbo",
                messages=messages,
                functions=functions,
                function_call="auto",
                temperature=0.3,
                max_tokens=800
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
                
                # 🔧 NUEVO: Si es hoy, ajustar al próximo horario válido
                if check_date.date() == now.date():
                    min_start = now + timedelta(hours=1)
                    if current_time < min_start:
                        # 🔧 REDONDEAR al próximo horario válido
                        current_time = self._round_to_next_valid_time(min_start, servicio)
                
                # Generar slots
                while current_time + timedelta(minutes=duracion_minutos) <= end_work:
                    # Verificar si el slot está libre
                    slot_end = current_time + timedelta(minutes=duracion_minutos)
                    
                    is_free = True
                    for event in events:
                        if not isinstance(event, dict):
                            continue
                        event_start_info = event.get('start', {})
                        event_end_info = event.get('end', {})
                        if not event_start_info or not event_end_info:
                            continue
                        try:
                            if 'dateTime' in event_start_info:
                                event_start_str = event_start_info['dateTime']
                            elif 'date' in event_start_info:
                                event_start_str = event_start_info['date'] + 'T00:00:00'
                            else:
                                continue
                            if 'dateTime' in event_end_info:
                                event_end_str = event_end_info['dateTime']
                            elif 'date' in event_end_info:
                                event_end_str = event_end_info['date'] + 'T23:59:59'
                            else:
                                continue
                            event_start = datetime.fromisoformat(event_start_str.replace('Z', '+00:00'))
                            event_end = datetime.fromisoformat(event_end_str.replace('Z', '+00:00'))
                            event_start = event_start.astimezone(self.tz)
                            event_end = event_end.astimezone(self.tz)
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
                    increment = self._get_time_increment(servicio)
                    current_time += timedelta(minutes=increment)
            return available_slots
        except Exception as e:
            print(f"❌ Error consultando Google Calendar: {e}")
            return self._generate_mock_slots(servicio, dias_adelante)

    def _round_to_next_valid_time(self, datetime_obj: datetime, servicio: dict) -> datetime:
        """🔧 CORREGIDA: Redondear según configuración real de la BD"""
        solo_horas_exactas = servicio.get('solo_horas_exactas', False)
        intervalo_entre_turnos = servicio.get('intervalo_entre_turnos', 15)
        current_minute = datetime_obj.minute
        next_hour = datetime_obj.hour
        if solo_horas_exactas:
            # Solo horarios en punto (00) y media (30)
            if current_minute < 30:
                next_minute = 30
            else:
                next_minute = 0
                next_hour += 1
        else:
            next_minute = ((current_minute // intervalo_entre_turnos) + 1) * intervalo_entre_turnos
            if next_minute >= 60:
                next_minute = 0
                next_hour += 1
        rounded_time = datetime_obj.replace(
            hour=next_hour,
            minute=next_minute,
            second=0,
            microsecond=0
        )
        print(f"🔧 DEBUG: Redondeando {datetime_obj.strftime('%H:%M')} → {rounded_time.strftime('%H:%M')}")
        print(f"🔧 DEBUG: Config: Solo horas exactas={solo_horas_exactas}, Intervalo={intervalo_entre_turnos} min")
        return rounded_time

    def _get_business_context(self, tenant: Tenant, db: Session) -> dict:
        """Obtener contexto del negocio: servicios, empleados, configuración general"""
        servicios = db.query(Servicio).filter(Servicio.tenant_id == tenant.id).all()
        empleados = db.query(Empleado).filter(Empleado.tenant_id == tenant.id).all()
        return {
            "servicios": [
                {
                    "id": s.id,
                    "nombre": s.nombre,
                    "duracion": s.duracion,
                    "precio": s.precio,
                    "solo_horas_exactas": getattr(s, "solo_horas_exactas", False),
                    "intervalo_entre_turnos": getattr(s, "intervalo_entre_turnos", 15),
                    "calendar_id": getattr(s, "calendar_id", None),
                    "es_informativo": getattr(s, "es_informativo", False),
                    "mensaje_personalizado": getattr(s, "mensaje_personalizado", "")
                }
                for s in servicios
            ],
            "empleados": [
                {
                    "id": e.id,
                    "nombre": e.nombre,
                    "calendar_id": getattr(e, "calendar_id", None)
                }
                for e in empleados
            ],
            "tiene_empleados": len(empleados) > 0,
            "calendar_id_general": getattr(tenant, "calendar_id_general", None)
        }
    
    async def crear_reserva(self, servicio_id, fecha_hora, empleado_id, nombre_cliente, telefono, db: Session):
        try:
            fecha_dt = datetime.fromisoformat(fecha_hora)
            # Verificar duplicados
            reserva_existente = db.query(Reserva).filter(
                Reserva.servicio == servicio_id,
                Reserva.fecha_reserva == fecha_dt,
                Reserva.estado == "activo"
            ).first()
            if reserva_existente:
                return "❌ Ya existe una reserva activa para ese horario. Elige otro turno."

            servicio = db.query(Servicio).filter(Servicio.id == servicio_id).first()
            empleado = db.query(Empleado).filter(Empleado.id == empleado_id).first() if empleado_id else None
            fake_id = generar_fake_id()

            # Crear evento en Google Calendar
            event_id = ""
            try:
                if self.google_credentials:
                    credentials_info = json.loads(self.google_credentials)
                    credentials = service_account.Credentials.from_service_account_info(credentials_info)
                    service_gc = build('calendar', 'v3', credentials=credentials)
                    calendar_id = empleado.calendar_id if empleado and empleado.calendar_id else servicio.calendar_id
                    if not calendar_id:
                        calendar_id = 'primary'
                    event = {
                        'summary': f"{servicio.nombre} - {nombre_cliente}",
                        'description': f"Reserva generada por el sistema para {nombre_cliente} (tel: {telefono})",
                        'start': {
                            'dateTime': fecha_dt.isoformat(),
                            'timeZone': str(self.tz)
                        },
                        'end': {
                            'dateTime': (fecha_dt + timedelta(minutes=servicio.duracion)).isoformat(),
                            'timeZone': str(self.tz)
                        },
                    }
                    created_event = service_gc.events().insert(calendarId=calendar_id, body=event).execute()
                    event_id = created_event.get('id', '')
            except Exception as e:
                print(f"❌ Error creando evento en Google Calendar: {e}")

            nueva_reserva = Reserva(
                fake_id=fake_id,
                event_id=event_id,
                empresa=servicio.tenant.nombre,
                empleado_id=empleado.id if empleado else None,
                empleado_nombre=empleado.nombre if empleado else "Sistema",
                empleado_calendar_id=empleado.calendar_id if empleado else servicio.calendar_id,
                cliente_nombre=nombre_cliente,
                cliente_telefono=telefono,
                fecha_reserva=fecha_dt,
                servicio=servicio.nombre,
                estado="activo",
                cantidad=1
            )
            db.add(nueva_reserva)
            db.commit()
            return f"✅ Reserva confirmada para *{servicio.nombre}* el {fecha_dt.strftime('%A %d/%m a las %H:%M')}."
        except Exception as e:
            print(f"❌ Error creando reserva: {e}")
            return "😵 No pude confirmar la reserva. Intenta de nuevo."
    
    async def cancelar_reserva(self, codigo_reserva: str, telefono: str, db: Session) -> str:
        """Cancelar una reserva existente por código"""
        try:
            reserva = db.query(Reserva).filter(
                Reserva.fake_id == codigo_reserva,
                Reserva.cliente_telefono == telefono,
                Reserva.estado == "activo"
            ).first()
            if not reserva:
                return "❌ No encontré la reserva activa con ese código."

            # Eliminar evento en Google Calendar si existe
            try:
                if self.google_credentials and reserva.event_id:
                    credentials_info = json.loads(self.google_credentials)
                    credentials = service_account.Credentials.from_service_account_info(credentials_info)
                    service_gc = build('calendar', 'v3', credentials=credentials)
                    calendar_id = reserva.empleado_calendar_id or reserva.empresa or 'primary'
                    service_gc.events().delete(calendarId=calendar_id, eventId=reserva.event_id).execute()
            except Exception as e:
                print(f"❌ Error eliminando evento en Google Calendar: {e}")

            reserva.estado = "cancelado"
            db.commit()
            return f"✅ Reserva cancelada correctamente.\nCódigo: {codigo_reserva}"
        except Exception as e:
            print(f"❌ Error cancelando reserva: {e}")
            return "😵 No pude cancelar la reserva. Intenta de nuevo."
    
    def _format_servicios_with_real_ids(self, servicios: list) -> str:
        """
        Devuelve una lista de servicios con sus IDs reales para mostrar al usuario.
        """
        if not servicios:
            return "No hay servicios disponibles."
        lines = []
        for s in servicios:
            lines.append(f"{s['id']}: {s['nombre']}")
        return "\n".join(lines)
    
    def mostrar_servicios(self, business_context: dict) -> str:
        """Devuelve la lista de servicios disponibles para mostrar al cliente."""
        return f"✨ Servicios disponibles:\n{self._format_servicios_with_real_ids(business_context['servicios'])}\n\n💬 Escribe el número o nombre del servicio que te interesa."
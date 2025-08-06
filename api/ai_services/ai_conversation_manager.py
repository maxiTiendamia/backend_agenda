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
    # Eliminar lógica de búsqueda, reserva y cancelación de turnos. Solo delega a calendar_utils.
    async def _execute_ai_function(self, function_call, telefono, business_context, tenant, db):
        """Delegar funciones de turnos a calendar_utils"""
        from api.utils import calendar_utils
        name = function_call["name"]
        args = function_call["args"]
        if name == "buscar_horarios_servicio":
            # Buscar horarios disponibles usando calendar_utils
            servicio_id = args["servicio_id"]
            servicio = db.query(Servicio).filter(Servicio.id == servicio_id).first()
            if not servicio:
                return "❌ Servicio no encontrado."
            slots = calendar_utils.get_available_slots_for_service(
                servicio,
                intervalo_entre_turnos=getattr(servicio, "intervalo_entre_turnos", 15),
                max_days=7,
                max_turnos=10,
                credentials_json=self.google_credentials
            )
            if not slots:
                return f"😔 No hay horarios disponibles para {servicio.nombre} esta semana."
            respuesta = f"✨ Horarios disponibles para {servicio.nombre}\n"
            for i, slot in enumerate(slots[:8], 1):
                hora_str = slot.strftime('%d/%m %H:%M')
                respuesta += f"{i}. {hora_str}\n"
            respuesta += "\n💬 Escribe el número o la hora que prefieres."
            return respuesta
        elif name == "crear_reserva":
            servicio_id = args["servicio_id"]
            servicio = db.query(Servicio).filter(Servicio.id == servicio_id).first()
            if not servicio:
                return "❌ Servicio no encontrado."
            fecha_hora = args["fecha_hora"]
            nombre_cliente = args["nombre_cliente"]
            slot_dt = datetime.fromisoformat(fecha_hora)
            try:
                event_id = calendar_utils.create_event_for_service(
                    servicio,
                    slot_dt,
                    telefono,
                    self.google_credentials,
                    nombre_cliente
                )
                return f"✅ Reserva confirmada para {servicio.nombre} el {slot_dt.strftime('%d/%m %H:%M')} a nombre de {nombre_cliente}."
            except Exception as e:
                return f"❌ Error al crear la reserva: {e}"
        elif name == "cancelar_reserva":
            codigo_reserva = args["codigo_reserva"]
            calendar_id = business_context.get("calendar_id_general", "primary")
            try:
                ok = calendar_utils.cancelar_evento_google(
                    calendar_id,
                    codigo_reserva,
                    self.google_credentials
                )
                if ok:
                    return "✅ Reserva cancelada correctamente."
                else:
                    return "❌ No se pudo cancelar la reserva."
            except Exception as e:
                return f"❌ Error al cancelar la reserva: {e}"
        return "Función no implementada."

    def _generar_respuesta_fallback(self, mensaje, user_history, business_context):
        """Respuesta fallback si falla la IA"""
        return "Disculpa, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?"
    def __init__(self, api_key, redis_client):
        self.client = openai.OpenAI(api_key=api_key)
        self.redis_client = redis_client
        self.tz = pytz.timezone("America/Montevideo")
        self.webconnect_url = os.getenv("webconnect_url", "http://195.26.250.62:3000")  
        self.google_credentials = os.getenv("GOOGLE_CREDENTIALS_JSON")

    def _get_time_increment(self, servicio):
        """
        Devuelve el incremento de minutos entre turnos según la configuración del servicio o Tenant.
        """
        intervalo = getattr(servicio, 'intervalo_entre_turnos', None)
        if intervalo:
            return int(intervalo)
        return 15

    def _traducir_dia(self, dia_en):
        """Traduce el nombre de un día de la semana de inglés a español."""
        dias = {
            'monday': 'lunes',
            'tuesday': 'martes',
            'wednesday': 'miércoles',
            'thursday': 'jueves',
            'friday': 'viernes',
            'saturday': 'sábado',
            'sunday': 'domingo',
            'lunes': 'lunes',
            'martes': 'martes',
            'miércoles': 'miércoles',
            'miercoles': 'miércoles',
            'jueves': 'jueves',
            'viernes': 'viernes',
            'sábado': 'sábado',
            'sabado': 'sábado',
            'domingo': 'domingo'
        }
        return dias.get(dia_en.lower(), dia_en)
    
    def _normalize_datetime(self, dt):
        """🔧 NORMALIZAR datetime para que siempre tenga timezone"""
        if dt is None:
            return None
        
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        return dt.astimezone(self.tz)
    
    def _preguntar_dia_disponible(self, servicio_seleccionado, telefono):
        """Pregunta al usuario por el día que desea para el servicio seleccionado."""
        tipo_servicio = "🎾" if "padel" in servicio_seleccionado['nombre'].lower() else "✨"
        respuesta = f"{tipo_servicio} *{servicio_seleccionado['nombre']}*\n"
        respuesta += "\n📅 ¿Para qué día te gustaría reservar?\n"
        respuesta += "Puedes responder con 'hoy', 'mañana', o el nombre de un día (ejemplo: 'viernes').\n"
        respuesta += "\n💬 Escribe el día que prefieres."
        return respuesta
    
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
            # Limpiar estado si es un saludo
            mensaje_stripped = mensaje.strip().lower()
            saludos = ["hola", "buenas", "buenos días", "buenas tardes", "buenas noches", "hey", "holi", "holaa", "saludos"]
            if any(mensaje_stripped.startswith(s) for s in saludos):
                # Limpiar selección de servicio y slots
                self.redis_client.delete(f"servicio_seleccionado:{telefono}")
                for key in self.redis_client.scan_iter(f"slots:{telefono}:*"):
                    self.redis_client.delete(key)
                self.redis_client.delete(f"slot_seleccionado:{telefono}")

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
            return self._generar_respuesta_fallback(mensaje, None, None)

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

    async def _ai_process_conversation_natural(self, mensaje, telefono, conversation_history, user_history, business_context, tenant, db):
        """🔧 CORREGIDO: Procesamiento de IA más natural y contextual"""
        
        mensaje_stripped = mensaje.strip().lower()
        
        # 🔧 VERIFICAR PRIMERO SI TIENE SERVICIO SELECCIONADO Y HORARIOS DISPONIBLES
        servicio_key = f"servicio_seleccionado:{telefono}"
        servicio_guardado_str = self.redis_client.get(servicio_key)
        
        if servicio_guardado_str:
            servicio_guardado = json.loads(servicio_guardado_str)
            slots_key = f"slots:{telefono}:{servicio_guardado['id']}"
            slots_data_str = self.redis_client.get(slots_key)
            if slots_data_str:
                slots_data = json.loads(slots_data_str)
                # 1. Selección de horario por número
                if mensaje_stripped.isdigit():
                    try:
                        slot_numero = int(mensaje_stripped)
                        if 1 <= slot_numero <= len(slots_data):
                            slot_seleccionado = slots_data[slot_numero - 1]
                            # Guardar slot seleccionado en Redis para el paso siguiente
                            self.redis_client.set(f"slot_seleccionado:{telefono}", json.dumps(slot_seleccionado), ex=600)
                            return (
                                f"✅ Elegiste:\n\n🎾 *{servicio_guardado['nombre']}*"
                                f"\n📅 {datetime.fromisoformat(slot_seleccionado['fecha_hora']).strftime('%A %d/%m a las %H:%M')}"
                                "\n\n👤 Para confirmar, por favor escribe tu *nombre completo*."
                            )
                        else:
                            return f"❌ Elige un número entre 1 y {len(slots_data)}."
                    except ValueError:
                        return "❌ No entendí el número. Intenta de nuevo."
                # 2. Selección de horario por hora (ej: "19:00")
                time_pattern = r'\b(\d{1,2}):(\d{2})\b'
                time_match = re.search(time_pattern, mensaje_stripped)
                if time_match:
                    hora_buscada = f"{time_match.group(1).zfill(2)}:{time_match.group(2)}"
                    for slot in slots_data:
                        slot_hora = datetime.fromisoformat(slot['fecha_hora']).strftime('%H:%M')
                        if slot_hora == hora_buscada:
                            self.redis_client.set(f"slot_seleccionado:{telefono}", json.dumps(slot), ex=600)
                            return (
                                f"✅ Elegiste:\n\n🎾 *{servicio_guardado['nombre']}*"
                                f"\n📅 {datetime.fromisoformat(slot['fecha_hora']).strftime('%A %d/%m a las %H:%M')}"
                                "\n\n👤 Para confirmar, por favor escribe tu *nombre completo*."
                            )
                    return f"❌ No encontré el horario {hora_buscada}. Elige uno de los horarios numerados."
                # 3. Confirmación de reserva (nombre completo)
                slot_seleccionado_str = self.redis_client.get(f"slot_seleccionado:{telefono}")
                if slot_seleccionado_str and len(mensaje_stripped.split()) >= 2:
                    slot_seleccionado = json.loads(slot_seleccionado_str)
                    nombre_cliente = mensaje.strip()
                    # Llamar a la función de calendar_utils para crear la reserva
                    from api.utils import calendar_utils
                    # Obtener el objeto modelo Servicio desde la base de datos
                    servicio_modelo = db.query(Servicio).filter(Servicio.id == servicio_guardado['id']).first()
                    if not servicio_modelo:
                        return "❌ Servicio no disponible. Intenta de nuevo."
                    slot_dt = datetime.fromisoformat(slot_seleccionado['fecha_hora'])
                    try:
                        event_id = calendar_utils.create_event_for_service(
                            servicio_modelo,
                            slot_dt,
                            telefono,
                            self.google_credentials,
                            nombre_cliente
                        )
                        # Limpiar selección en Redis
                        self.redis_client.delete(f"servicio_seleccionado:{telefono}")
                        self.redis_client.delete(f"slots:{telefono}:{servicio_guardado['id']}")
                        self.redis_client.delete(f"slot_seleccionado:{telefono}")
                        return (
                            f"✅ Reserva confirmada para *{servicio_guardado['nombre']}*"
                            f"\n📅 {slot_dt.strftime('%A %d/%m %H:%M')}"
                            f"\n👤 A nombre de: {nombre_cliente}\n\n¡Gracias por reservar! 😊"
                        )
                    except Exception as e:
                        return f"❌ Error al crear la reserva: {e}"
                # Si no se reconoce el mensaje, pedir nombre completo
                if slot_seleccionado_str:
                    return "👤 Por favor, escribe tu *nombre completo* para confirmar la reserva."
            # Si tiene servicio pero no slots, está eligiendo día
            dia_detectado = self._detectar_dia_mensaje(mensaje_stripped)
            if dia_detectado:
                # Buscar horarios disponibles para el día elegido
                from api.utils import calendar_utils
                # Obtener contexto actualizado del negocio (servicios y empleados)
                business_context = self._get_business_context(tenant, db)
                servicio_guardado_dict = next((s for s in business_context["servicios"] if s["id"] == servicio_guardado["id"]), None)
                if not servicio_guardado_dict:
                    return "❌ Servicio no disponible. Intenta de nuevo."
                # Buscar el modelo Servicio por ID y pasar el modelo, no un dict
                servicio_modelo = db.query(Servicio).filter(Servicio.id == servicio_guardado["id"]).first()
                if not servicio_modelo:
                    return "❌ Servicio no disponible. Intenta de nuevo."
                slots = calendar_utils.get_available_slots_for_service(
                    servicio_modelo,
                    intervalo_entre_turnos=getattr(servicio_modelo, "intervalo_entre_turnos", 15),
                    max_days=7,
                    max_turnos=10,
                    credentials_json=self.google_credentials
                )
                # Filtrar slots por día
                tz = pytz.timezone("America/Montevideo")
                now = datetime.now(tz)
                if dia_detectado == "hoy":
                    dia_objetivo = now.date()
                elif dia_detectado == "mañana":
                    dia_objetivo = (now + timedelta(days=1)).date()
                else:
                    dias_semana = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
                    idx = dias_semana.index(dia_detectado)
                    hoy_idx = now.weekday()
                    dias_hasta = (idx - hoy_idx) % 7
                    dia_objetivo = (now + timedelta(days=dias_hasta)).date()
                slots_dia = [s for s in slots if s['fecha'].date() == dia_objetivo]
                if not slots_dia:
                    return f"😔 No hay horarios disponibles para *{servicio_guardado_dict['nombre']}* el {dia_detectado}.\n¿Quieres elegir otro día?"
                # Guardar slots en Redis
                slots_key = f"slots:{telefono}:{servicio_guardado_dict['id']}"
                slots_data = [
                    {
                        "numero": i,
                        "fecha_hora": slot['fecha'].isoformat(),
                        "empleado_id": None,
                        "empleado_nombre": "Sistema"
                    }
                    for i, slot in enumerate(slots_dia[:8], 1)
                ]
                self.redis_client.set(slots_key, json.dumps(slots_data), ex=1800)
                return (f"🎾 *Horarios para {servicio_guardado_dict['nombre']}* el {dia_detectado}:\n"
                        + "\n".join([f"{i}. {datetime.fromisoformat(s['fecha_hora']).strftime('%H:%M')}" for i, s in enumerate(slots_data, 1)])
                        + "\n\n💬 Escribe el número o la hora que prefieres.")
        
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
        """Devuelve la lista de servicios disponibles para mostrar al cliente (siempre actualizada)."""
        servicios = business_context["servicios"]
        if not servicios:
            return "No hay servicios disponibles en este momento."
        # Mostrar servicios numerados y por nombre, sin duplicados ni nombres erróneos
        lines = []
        for idx, s in enumerate(servicios, 1):
            lines.append(f"{idx}. {s['nombre']}")
        return f"✨ Servicios disponibles:\n" + "\n".join(lines) + "\n\n💬 Escribe el número o nombre del servicio que te interesa."

    def _get_business_context(self, tenant, db):
        """Obtener contexto del negocio: servicios y empleados desde models.py"""
        from api.app.models import Servicio, Empleado
        servicios_db = db.query(Servicio).filter(Servicio.tenant_id == tenant.id).all()
        empleados_db = db.query(Empleado).filter(Empleado.tenant_id == tenant.id).all()
        
        # Convertir objetos a diccionarios
        servicios = []
        for s in servicios_db:
            servicios.append({
                "id": s.id,
                "nombre": s.nombre,
                "precio": getattr(s, "precio", 0),
                "duracion": getattr(s, "duracion", 60),
                "es_informativo": getattr(s, "es_informativo", False),
                "mensaje_personalizado": getattr(s, "mensaje_personalizado", ""),
                "intervalo_entre_turnos": getattr(s, "intervalo_entre_turnos", 15)
            })
        
        empleados = []
        for e in empleados_db:
            empleados.append({
                "id": e.id,
                "nombre": e.nombre,
                "email": getattr(e, "email", ""),
                "telefono": getattr(e, "telefono", "")
            })
        
        return {
            "servicios": servicios,
            "empleados": empleados,
            "tiene_empleados": len(empleados) > 0,
            "calendar_id_general": getattr(tenant, "calendar_id_general", None)
        }

def _parse_working_hours(wh):
    if wh is None:
        return None
    if isinstance(wh, str):
        try:
            return json.loads(wh)
        except Exception:
            return None
    return wh
import openai
import json
from datetime import datetime, timedelta
import pytz
from sqlalchemy.orm import Session
from api.app.models import Tenant, Servicio, Empleado, Reserva, BlockedNumber
from api.utils.generador_fake_id import generar_fake_id
from google.oauth2 import service_account
from googleapiclient.discovery import build
import redis
import os
import httpx
import re
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

class AIConversationManager:
    def __init__(self, api_key, redis_client):
        self.client = openai.OpenAI(api_key=api_key)
        self.redis_client = redis_client
        self.tz = pytz.timezone("America/Montevideo")
        self.webconnect_url = os.getenv("webconnect_url", "http://195.26.250.62:3000")
    
    async def process_message(self, telefono: str, mensaje: str, cliente_id: int, db: Session):
        """
        🤖 IA procesa TODA la conversación y maneja todos los flujos simultáneamente
        """
        try:
            # 1. Verificar si el número está bloqueado
            if self._is_blocked_number(telefono, cliente_id, db):
                print(f"🚫 Número {telefono} bloqueado para cliente {cliente_id}")
                return ""
            
            # 2. Obtener información completa del negocio
            tenant = db.query(Tenant).filter_by(id=cliente_id).first()
            if not tenant:
                return "⚠️ Cliente no encontrado."
            
            # 3. Verificar modo humano
            if self._is_human_mode(telefono):
                if mensaje.lower() in ["bot", "volver", "asistente"]:
                    self._set_bot_mode(telefono)
                    return "🤖 El asistente virtual está activo nuevamente. ¿En qué puedo ayudarte?"
                else:
                    # Notificar a humano y no responder
                    await self._notify_human_support(cliente_id, telefono, mensaje)
                    return ""
            
            # 4. Solicitud de ayuda humana
            if any(word in mensaje.lower() for word in ["ayuda", "asesor", "humano", "persona"]):
                self._set_human_mode(telefono)
                await self._notify_human_support(cliente_id, telefono, mensaje)
                return "🚪 Un asesor te responderá a la brevedad. Puedes escribir \"Bot\" para volver al asistente automático."
            
            # 5. Obtener contexto completo para la IA
            conversation_context = self._get_business_context(tenant, db)
            user_history = self._get_user_history(telefono, db)
            
            # 6. IA procesa el mensaje con contexto completo
            ai_response = await self._ai_process_conversation(
                mensaje=mensaje,
                telefono=telefono,
                conversation_context=conversation_context,
                user_history=user_history,
                tenant=tenant,
                db=db
            )
            
            return ai_response
            
        except Exception as e:
            print(f"❌ Error en AI manager: {e}")
            return "Disculpa, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?"
    
    def _is_blocked_number(self, telefono: str, cliente_id: int, db: Session) -> bool:
        """Verificar si el número está bloqueado"""
        blocked = db.query(BlockedNumber).filter(
            BlockedNumber.telefono == telefono,
            BlockedNumber.cliente_id == cliente_id
        ).first()
        return blocked is not None
    
    def _set_human_mode(self, telefono: str):
        """Activar modo humano"""
        self.redis_client.setex(f"human_mode:{telefono}", 3600, "true")
    
    def _set_bot_mode(self, telefono: str):
        """Activar modo bot"""
        self.redis_client.delete(f"human_mode:{telefono}")
    
    def _is_human_mode(self, telefono: str) -> bool:
        """Verificar si está en modo humano"""
        return bool(self.redis_client.get(f"human_mode:{telefono}"))
    
    async def _notify_human_support(self, cliente_id: int, telefono: str, mensaje: str):
        """Notificar solicitud de atención humana"""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self.webconnect_url}/notificar-chat-humano",
                    json={
                        "cliente_id": cliente_id,
                        "telefono": telefono,
                        "mensaje": mensaje,
                        "tipo": "solicitud_ayuda"
                    },
                    timeout=5.0
                )
            print(f"✅ Solicitud de ayuda registrada - Cliente {cliente_id}: {telefono}")
        except Exception as e:
            print(f"⚠️ Error registrando solicitud de ayuda: {e}")
    
    def _get_business_context(self, tenant: Tenant, db: Session) -> dict:
        """Obtener contexto completo del negocio para la IA"""
        servicios = db.query(Servicio).filter_by(tenant_id=tenant.id).all()
        empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
        
        return {
            "negocio": {
                "nombre": tenant.comercio,
                "direccion": getattr(tenant, 'direccion', ''),
                "telefono": getattr(tenant, 'telefono', ''),
                "informacion": getattr(tenant, 'informacion_local', ''),
                "intervalo_turnos": getattr(tenant, 'intervalo_entre_turnos', 20)
            },
            "servicios": [
                {
                    "id": s.id,
                    "nombre": s.nombre,
                    "duracion": getattr(s, 'duracion', 0),
                    "precio": getattr(s, 'precio', 0),
                    "cantidad": getattr(s, 'cantidad', 1),
                    "es_informativo": getattr(s, 'es_informativo', False),
                    "mensaje_personalizado": getattr(s, 'mensaje_personalizado', ''),
                    "tiene_calendario": bool(getattr(s, 'calendar_id', None)),
                    "calendar_id": getattr(s, 'calendar_id', ''),
                    "horarios_trabajo": getattr(s, 'working_hours', '{}'),
                    "solo_horas_exactas": getattr(s, 'solo_horas_exactas', False),
                    "turnos_consecutivos": getattr(s, 'turnos_consecutivos', False)
                }
                for s in servicios
            ],
            "empleados": [
                {
                    "id": e.id,
                    "nombre": e.nombre,
                    "tiene_calendario": bool(getattr(e, 'calendar_id', None)),
                    "calendar_id": getattr(e, 'calendar_id', ''),
                    "horarios_trabajo": getattr(e, 'working_hours', '{}')
                }
                for e in empleados
            ],
            "credenciales_google": tenant.service_account_info
        }
    
    def _get_user_history(self, telefono: str, db: Session) -> dict:
        """Obtener historial del usuario"""
        reservas_activas = db.query(Reserva).filter(
            Reserva.cliente_telefono == telefono,
            Reserva.estado == "activo"
        ).all()
        
        return {
            "reservas_activas": [
                {
                    "codigo": r.fake_id,
                    "servicio": r.servicio,
                    "empleado": r.empleado_nombre,
                    "fecha": r.fecha_reserva.strftime("%d/%m %H:%M") if r.fecha_reserva else "",
                    "puede_cancelar": r.fecha_reserva > datetime.now(self.tz) + timedelta(hours=1) if r.fecha_reserva else False
                }
                for r in reservas_activas
            ]
        }
    
    async def _ai_process_conversation(self, mensaje: str, telefono: str, conversation_context: dict, user_history: dict, tenant: Tenant, db: Session) -> str:
        """
        🤖 IA procesa la conversación con contexto completo del negocio
        """
        try:
            # Construir prompt inteligente para la IA
            system_prompt = f"""
Eres el asistente virtual especializado de {conversation_context['negocio']['nombre']}.

INFORMACIÓN COMPLETA DEL NEGOCIO:
{json.dumps(conversation_context, indent=2, ensure_ascii=False)}

HISTORIAL DEL CLIENTE:
{json.dumps(user_history, indent=2, ensure_ascii=False)}

FUNCIONES DISPONIBLES:
1. mostrar_servicios - Mostrar lista de servicios disponibles
2. buscar_horarios_servicio - Buscar horarios para servicios con calendario propio
3. buscar_horarios_empleado - Buscar horarios con empleado específico
4. crear_reserva - Crear reserva (automáticamente detecta si es servicio o empleado)
5. cancelar_reserva - Cancelar reserva existente
6. mostrar_info_servicio - Mostrar información de servicios informativos
7. saludar_cliente - Mensaje de bienvenida personalizado

REGLAS DE COMPORTAMIENTO:
- Responde SIEMPRE de forma amigable y profesional
- Si el cliente saluda por primera vez, usa saludar_cliente
- Si dice "turno", "reservar", "agendar", usa mostrar_servicios
- Para servicios informativos, usa mostrar_info_servicio
- Para servicios con calendario, busca horarios inteligentemente según preferencias del cliente
- Para empleados, pregunta cuál prefiere si hay varios
- Detecta automáticamente cancelaciones (formato: "cancelar CODIGO")
- Usa emojis apropiados pero sin exceso
- Detecta preferencias de horario (mañana, tarde, hoy, mañana, etc.)
- Siempre confirma datos antes de crear reservas
- Si no entiendes algo, pregunta de forma amigable

INSTRUCCIONES ESPECIALES:
- Para horarios, considera las preferencias del cliente (urgencia, hora del día, etc.)
- Si un servicio tiene empleados Y calendario propio, pregunta la preferencia
- Mantén conversaciones naturales, no robóticas
- Adapta el tono al cliente (formal/informal según su mensaje)
"""

            # Llamar a OpenAI con funciones
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",  # Usar 3.5 para costos controlados
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Cliente: '{mensaje}'"}
                ],
                functions=[
                    {
                        "name": "saludar_cliente",
                        "description": "Generar mensaje de bienvenida personalizado",
                        "parameters": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "mostrar_servicios",
                        "description": "Mostrar lista de servicios disponibles",
                        "parameters": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "buscar_horarios_servicio",
                        "description": "Buscar horarios inteligentes para servicio",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "servicio_id": {"type": "integer"},
                                "preferencia_horario": {"type": "string", "description": "mañana, tarde, noche, hoy, mañana, etc."},
                                "cantidad_horarios": {"type": "integer", "default": 10}
                            },
                            "required": ["servicio_id"]
                        }
                    },
                    {
                        "name": "buscar_horarios_empleado",
                        "description": "Buscar horarios con empleado específico",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "empleado_id": {"type": "integer"},
                                "servicio_id": {"type": "integer"},
                                "preferencia_horario": {"type": "string"},
                                "cantidad_horarios": {"type": "integer", "default": 10}
                            },
                            "required": ["empleado_id", "servicio_id"]
                        }
                    },
                    {
                        "name": "crear_reserva",
                        "description": "Crear reserva inteligente",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "servicio_id": {"type": "integer"},
                                "empleado_id": {"type": "integer", "description": "opcional"},
                                "slot_seleccionado": {"type": "integer", "description": "número del horario elegido"},
                                "nombre_cliente": {"type": "string"}
                            },
                            "required": ["servicio_id", "slot_seleccionado", "nombre_cliente"]
                        }
                    },
                    {
                        "name": "cancelar_reserva",
                        "description": "Cancelar reserva existente",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "codigo_reserva": {"type": "string"}
                            },
                            "required": ["codigo_reserva"]
                        }
                    },
                    {
                        "name": "mostrar_info_servicio",
                        "description": "Mostrar información de servicio informativo",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "servicio_id": {"type": "integer"}
                            },
                            "required": ["servicio_id"]
                        }
                    }
                ],
                function_call="auto",
                temperature=0.7,
                max_tokens=1000
            )
            
            message = response.choices[0].message
            
            # Si la IA quiere ejecutar una función
            if message.function_call:
                function_result = await self._execute_ai_function(
                    message.function_call,
                    telefono,
                    conversation_context,
                    tenant,
                    db
                )
                return function_result
            else:
                # Respuesta directa de la IA
                return message.content
            
        except Exception as e:
            print(f"❌ Error en procesamiento IA: {e}")
            return "Disculpa, tuve un problema procesando tu solicitud. ¿Podrías intentar de nuevo?"
    
    async def _execute_ai_function(self, function_call, telefono: str, context: dict, tenant: Tenant, db: Session) -> str:
        """Ejecutar función solicitada por la IA"""
        try:
            function_name = function_call.name
            arguments = json.loads(function_call.arguments) if function_call.arguments else {}
            
            if function_name == "saludar_cliente":
                return self._generar_saludo_personalizado(context)
            
            elif function_name == "mostrar_servicios":
                return self._mostrar_servicios_disponibles(context)
            
            elif function_name == "buscar_horarios_servicio":
                return await self._buscar_horarios_servicio_inteligente(arguments, context, telefono, db)
            
            elif function_name == "buscar_horarios_empleado":
                return await self._buscar_horarios_empleado_inteligente(arguments, context, telefono, db)
            
            elif function_name == "crear_reserva":
                return await self._crear_reserva_inteligente(arguments, telefono, context, tenant, db)
            
            elif function_name == "cancelar_reserva":
                return await self._cancelar_reserva_inteligente(arguments, telefono, tenant, db)
            
            elif function_name == "mostrar_info_servicio":
                return self._mostrar_info_servicio_detallada(arguments, context)
            
            else:
                return "No pude procesar esa función."
                
        except Exception as e:
            print(f"❌ Error ejecutando función IA: {e}")
            return "Tuve un problema procesando tu solicitud."
    
    def _generar_saludo_personalizado(self, context: dict) -> str:
        """Generar saludo personalizado con información del negocio"""
        negocio = context['negocio']
        mensaje = f"¡Hola! 👋 Soy el asistente virtual de *{negocio['nombre']}*\n\n"
        
        if negocio.get('informacion'):
            mensaje += f"ℹ️ *Sobre nosotros:*\n{negocio['informacion']}\n\n"
        
        if negocio.get('direccion'):
            mensaje += f"📍 *Ubicación:* {negocio['direccion']}\n\n"
        
        mensaje += "🎯 *¿En qué puedo ayudarte?*\n\n"
        mensaje += "🔹 Escribe *\"Turno\"* o *\"Reservar\"* para agendar\n"
        mensaje += "🔹 Escribe *\"Ayuda\"* para hablar con un asesor\n"
        
        return mensaje
    
    def _mostrar_servicios_disponibles(self, context: dict) -> str:
        """Mostrar lista inteligente de servicios"""
        servicios = context['servicios']
        
        if not servicios:
            return "⚠️ No hay servicios disponibles en este momento."
        
        # Separar por tipo
        servicios_reservables = []
        servicios_informativos = []
        
        for s in servicios:
            if s['es_informativo']:
                servicios_informativos.append(s)
            else:
                servicios_reservables.append(s)
        
        mensaje = "🎯 *Servicios disponibles:*\n\n"
        
        # Servicios reservables
        if servicios_reservables:
            mensaje += "📅 *Para reservar turnos:*\n"
            for i, s in enumerate(servicios_reservables, 1):
                mensaje += f"{i}. *{s['nombre']}*"
                if s['duracion'] and s['precio']:
                    mensaje += f" ({s['duracion']} min - ${s['precio']})"
                mensaje += "\n"
            mensaje += "\n"
        
        # Servicios informativos
        if servicios_informativos:
            mensaje += "ℹ️ *Para información:*\n"
            for i, s in enumerate(servicios_informativos, len(servicios_reservables) + 1):
                mensaje += f"{i}. *{s['nombre']}*\n"
            mensaje += "\n"
        
        mensaje += "💬 Responde con el número del servicio que te interesa."
        
        return mensaje
    
    async def _buscar_horarios_servicio_inteligente(self, args: dict, context: dict, telefono: str, db: Session) -> str:
        """Buscar horarios inteligentes para servicio"""
        try:
            servicio_id = args["servicio_id"]
            preferencia = args.get("preferencia_horario", "")
            cantidad = args.get("cantidad_horarios", 10)
            
            # Encontrar servicio
            servicio_info = next((s for s in context['servicios'] if s['id'] == servicio_id), None)
            if not servicio_info or not servicio_info['tiene_calendario']:
                return "❌ Servicio no encontrado o no disponible para reservas online."
            
            # Generar horarios inteligentes
            slots = await self._generar_horarios_inteligentes(
                servicio_info, 
                context['credenciales_google'], 
                preferencia,
                cantidad
            )
            
            if not slots:
                return f"😔 No hay horarios disponibles para *{servicio_info['nombre']}* próximamente."
            
            # Guardar slots en Redis para posterior uso
            slots_data = [s.isoformat() for s in slots]
            self.redis_client.setex(
                f"slots:{telefono}:{servicio_id}", 
                300,  # 5 minutos
                json.dumps(slots_data)
            )
            
            # Formatear respuesta
            mensaje = f"📅 *{servicio_info['nombre']}* - Horarios disponibles:\n\n"
            for i, slot in enumerate(slots, 1):
                dia_sem = slot.strftime('%A')
                dia_sem_es = self._traducir_dia(dia_sem)
                fecha_formatted = f"{dia_sem_es} {slot.strftime('%d/%m - %H:%M')}"
                mensaje += f"{i}. {fecha_formatted}\n"
            
            mensaje += f"\n💬 ¿Cuál te conviene? Responde: **reservar {servicio_id} [número] [tu nombre]**"
            mensaje += f"\nEjemplo: *reservar {servicio_id} 1 Juan Pérez*"
            
            return mensaje
            
        except Exception as e:
            print(f"❌ Error buscando horarios servicio: {e}")
            return "Tuve un problema buscando horarios. ¿Podrías intentar de nuevo?"
    
    async def _generar_horarios_inteligentes(self, servicio_info: dict, credentials_json: str, preferencia: str, cantidad: int) -> list:
        """
        🤖 Generar horarios inteligentes basados en preferencias
        """
        try:
            # Configurar Google Calendar
            service_account_info = json.loads(credentials_json)
            credentials = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=['https://www.googleapis.com/auth/calendar']
            )
            calendar_service = build('calendar', 'v3', credentials=credentials)
            
            # Parsear horarios de trabajo
            working_hours = json.loads(servicio_info['horarios_trabajo']) if servicio_info['horarios_trabajo'] else {}
            
            if not working_hours:
                return []
            
            # Generar slots básicos
            now = datetime.now(self.tz)
            end_date = now + timedelta(days=14)  # 2 semanas
            all_slots = []
            
            # Determinar filtros según preferencia
            filtro_hora = self._determinar_filtro_horario(preferencia)
            filtro_urgencia = self._determinar_filtro_urgencia(preferencia)
            
            current_date = now.date()
            if filtro_urgencia == "hoy":
                end_date = now + timedelta(days=1)
            elif filtro_urgencia == "mañana":
                current_date = (now + timedelta(days=1)).date()
                end_date = now + timedelta(days=2)
            
            # Generar slots día por día
            while current_date <= end_date.date() and len(all_slots) < 100:
                day_name = current_date.strftime('%A').lower()
                
                if day_name in working_hours and working_hours[day_name]:
                    day_slots = self._generar_slots_dia(
                        current_date, 
                        working_hours[day_name], 
                        servicio_info,
                        filtro_hora
                    )
                    all_slots.extend(day_slots)
                
                current_date += timedelta(days=1)
            
            # Filtrar slots disponibles
            available_slots = []
            for slot in all_slots:
                if slot > now and self._verificar_disponibilidad_slot(
                    calendar_service, 
                    servicio_info['calendar_id'], 
                    slot, 
                    servicio_info['duracion']
                ):
                    available_slots.append(slot)
                    if len(available_slots) >= cantidad:
                        break
            
            return sorted(available_slots)
            
        except Exception as e:
            print(f"❌ Error generando horarios inteligentes: {e}")
            return []
    
    def _determinar_filtro_horario(self, preferencia: str) -> dict:
        """Determinar filtro de horario según preferencia"""
        pref = preferencia.lower()
        
        if "mañana" in pref:
            return {"inicio": 6, "fin": 12}
        elif "tarde" in pref:
            return {"inicio": 12, "fin": 18}
        elif "noche" in pref:
            return {"inicio": 18, "fin": 23}
        else:
            return {"inicio": 0, "fin": 24}  # Sin filtro
    
    def _determinar_filtro_urgencia(self, preferencia: str) -> str:
        """Determinar urgencia según preferencia"""
        pref = preferencia.lower()
        
        if "hoy" in pref or "ahora" in pref or "urgente" in pref:
            return "hoy"
        elif "mañana" in pref and "por la" not in pref:
            return "mañana"
        else:
            return "normal"
    
    def _generar_slots_dia(self, date, periods, servicio_info, filtro_hora):
        """Generar slots para un día específico con filtros"""
        slots = []
        
        try:
            # Normalizar períodos
            if isinstance(periods, list) and periods:
                if isinstance(periods[0], str) and '-' in periods[0]:
                    periods = [
                        {'from': p.split('-')[0].strip(), 'to': p.split('-')[1].strip()} 
                        for p in periods if '-' in p and p != "--:---:--"
                    ]
            
            for period in periods:
                if isinstance(period, dict) and 'from' in period:
                    period_slots = self._generar_slots_periodo(
                        period, date, servicio_info, filtro_hora
                    )
                    slots.extend(period_slots)
            
            return slots
            
        except Exception as e:
            print(f"❌ Error generando slots del día: {e}")
            return []
    
    def _generar_slots_periodo(self, period, date, servicio_info, filtro_hora):
        """Generar slots para un período con filtros inteligentes"""
        try:
            start_time_str = period['from']
            end_time_str = period['to']
            
            if start_time_str == "--:--" or end_time_str == "--:--":
                return []
            
            start_hour, start_minute = map(int, start_time_str.split(':'))
            end_hour, end_minute = map(int, end_time_str.split(':'))
            
            # Aplicar filtro de horario
            if filtro_hora["inicio"] <= 24:  # Si hay filtro
                start_hour = max(start_hour, filtro_hora["inicio"])
                end_hour = min(end_hour, filtro_hora["fin"])
                
                if start_hour >= end_hour:
                    return []
            
            period_start = self.tz.localize(
                datetime.combine(date, datetime.min.time().replace(hour=start_hour, minute=start_minute))
            )
            period_end = self.tz.localize(
                datetime.combine(date, datetime.min.time().replace(hour=end_hour, minute=end_minute))
            )
            
            if period_end <= period_start:
                return []
            
            slots = []
            current_time = period_start
            
            # Determinar intervalo
            if servicio_info.get('solo_horas_exactas'):
                interval = 60  # Solo horas exactas
                # Ajustar al próximo minuto 00
                if current_time.minute != 0:
                    current_time = current_time.replace(minute=0) + timedelta(hours=1)
            else:
                interval = 30  # Cada 30 minutos
            
            while current_time + timedelta(minutes=servicio_info['duracion']) <= period_end:
                if current_time > datetime.now(self.tz):
                    slots.append(current_time)
                current_time += timedelta(minutes=interval)
            
            return slots
            
        except Exception as e:
            print(f"❌ Error generando slots período: {e}")
            return []
    
    def _verificar_disponibilidad_slot(self, calendar_service, calendar_id, start_time, duration):
        """Verificar disponibilidad en Google Calendar"""
        try:
            end_time = start_time + timedelta(minutes=duration)
            
            events_result = calendar_service.events().list(
                calendarId=calendar_id,
                timeMin=start_time.isoformat(),
                timeMax=end_time.isoformat(),
                singleEvents=True
            ).execute()
            
            return len(events_result.get('items', [])) == 0
            
        except Exception as e:
            print(f"❌ Error verificando disponibilidad: {e}")
            return True  # En caso de error, asumir disponible
    
    async def _crear_reserva_inteligente(self, args: dict, telefono: str, context: dict, tenant: Tenant, db: Session) -> str:
        """Crear reserva de forma inteligente"""
        try:
            servicio_id = args["servicio_id"]
            slot_num = args["slot_seleccionado"] - 1  # Convertir a índice 0
            nombre_cliente = args["nombre_cliente"].strip().title()
            empleado_id = args.get("empleado_id")
            
            # Recuperar slots guardados
            slots_key = f"slots:{telefono}:{servicio_id}"
            slots_data = self.redis_client.get(slots_key)
            
            if not slots_data:
                return "❌ Los horarios expiraron. Por favor, solicita horarios nuevamente."
            
            slots = [datetime.fromisoformat(s) for s in json.loads(slots_data)]
            
            if slot_num < 0 or slot_num >= len(slots):
                return "❌ Número de horario inválido. Por favor, elige un número de la lista."
            
            slot_elegido = slots[slot_num]
            
            # Encontrar servicio
            servicio_info = next((s for s in context['servicios'] if s['id'] == servicio_id), None)
            if not servicio_info:
                return "❌ Servicio no encontrado."
            
            # Crear reserva
            if empleado_id:
                # Reserva con empleado
                empleado_info = next((e for e in context['empleados'] if e['id'] == empleado_id), None)
                event_id = await self._crear_evento_google(
                    empleado_info['calendar_id'], slot_elegido, telefono, nombre_cliente, 
                    servicio_info, context['credenciales_google']
                )
                empleado_nombre = empleado_info['nombre']
                calendar_id = empleado_info['calendar_id']
            else:
                # Reserva directa con servicio
                event_id = await self._crear_evento_google(
                    servicio_info['calendar_id'], slot_elegido, telefono, nombre_cliente, 
                    servicio_info, context['credenciales_google']
                )
                empleado_nombre = "(Servicio directo)"
                calendar_id = servicio_info['calendar_id']
            
            # Guardar en base de datos
            fake_id = generar_fake_id()
            reserva = Reserva(
                fake_id=fake_id,
                event_id=event_id,
                empresa=tenant.comercio,
                empleado_id=empleado_id,
                empleado_nombre=empleado_nombre,
                empleado_calendar_id=calendar_id,
                cliente_nombre=nombre_cliente,
                cliente_telefono=telefono,
                fecha_reserva=slot_elegido,
                servicio=servicio_info['nombre'],
                estado="activo"
            )
            db.add(reserva)
            db.commit()
            
            # Limpiar slots del cache
            self.redis_client.delete(slots_key)
            
            # Mensaje de confirmación
            dia_sem = slot_elegido.strftime('%A')
            dia_sem_es = self._traducir_dia(dia_sem)
            fecha_formatted = f"{dia_sem_es} {slot_elegido.strftime('%d/%m %H:%M')}"
            
            mensaje = f"✅ *{nombre_cliente}*, tu reserva fue confirmada!\n\n"
            mensaje += f"📅 *Fecha:* {fecha_formatted}\n"
            mensaje += f"🎯 *Servicio:* {servicio_info['nombre']}"
            
            if servicio_info['duracion']:
                mensaje += f" ({servicio_info['duracion']} min)"
            if servicio_info['precio']:
                mensaje += f"\n💰 *Precio:* ${servicio_info['precio']}"
            
            if empleado_id:
                mensaje += f"\n👤 *Profesional:* {empleado_nombre}"
            
            if context['negocio']['direccion']:
                mensaje += f"\n📍 *Dirección:* {context['negocio']['direccion']}"
            
            mensaje += f"\n\n🆔 *Código:* {fake_id}"
            mensaje += f"\n\n❌ *Para cancelar:* cancelar {fake_id}"
            
            return mensaje
            
        except Exception as e:
            print(f"❌ Error creando reserva: {e}")
            return "❌ No pude crear la reserva. Por favor, intenta de nuevo."
    
    async def _crear_evento_google(self, calendar_id, slot_dt, telefono, nombre_cliente, servicio_info, credentials_json):
        """Crear evento en Google Calendar"""
        try:
            service_account_info = json.loads(credentials_json)
            credentials = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=['https://www.googleapis.com/auth/calendar']
            )
            
            calendar_service = build('calendar', 'v3', credentials=credentials)
            
            end_time = slot_dt + timedelta(minutes=servicio_info['duracion'])
            
            event = {
                'summary': f'{servicio_info["nombre"]} - {nombre_cliente}',
                'description': f'Cliente: {nombre_cliente}\nTeléfono: {telefono}\nServicio: {servicio_info["nombre"]}',
                'start': {
                    'dateTime': slot_dt.isoformat(),
                    'timeZone': 'America/Montevideo',
                },
                'end': {
                    'dateTime': end_time.isoformat(),
                    'timeZone': 'America/Montevideo',
                },
            }
            
            event_result = calendar_service.events().insert(
                calendarId=calendar_id,
                body=event
            ).execute()
            
            return event_result.get('id')
            
        except Exception as e:
            print(f"❌ Error creando evento Google: {e}")
            raise e
    
    async def _cancelar_reserva_inteligente(self, args: dict, telefono: str, tenant: Tenant, db: Session) -> str:
        """Cancelar reserva de forma inteligente"""
        try:
            codigo = args["codigo_reserva"].upper()
            
            # Extraer código si viene en formato "cancelar CODIGO"
            if " " in codigo:
                codigo = codigo.split()[-1]
            
            reserva = db.query(Reserva).filter_by(
                fake_id=codigo,
                cliente_telefono=telefono,
                estado="activo"
            ).first()
            
            if not reserva:
                return "❌ No encontré esa reserva o ya fue cancelada. Verifica el código."
            
            # Verificar tiempo de cancelación
            if reserva.fecha_reserva <= datetime.now(self.tz) + timedelta(hours=1):
                return "⏰ No puedes cancelar con menos de 1 hora de anticipación. Contacta con el establecimiento."
            
            # Cancelar en Google Calendar
            try:
                service_account_info = json.loads(tenant.service_account_info)
                credentials = service_account.Credentials.from_service_account_info(
                    service_account_info,
                    scopes=['https://www.googleapis.com/auth/calendar']
                )
                calendar_service = build('calendar', 'v3', credentials=credentials)
                
                calendar_service.events().delete(
                    calendarId=reserva.empleado_calendar_id,
                    eventId=reserva.event_id
                ).execute()
                
            except Exception as e:
                print(f"⚠️ Error cancelando en Google Calendar: {e}")
                # Continuar con cancelación en BD aunque falle Google
            
            # Marcar como cancelado
            reserva.estado = "cancelado"
            db.commit()
            
            dia_sem = reserva.fecha_reserva.strftime('%A')
            dia_sem_es = self._traducir_dia(dia_sem)
            fecha_formatted = f"{dia_sem_es} {reserva.fecha_reserva.strftime('%d/%m %H:%M')}"
            
            return f"✅ Tu reserva *{codigo}* fue cancelada correctamente.\n\n📅 Era para: {fecha_formatted}\n🎯 Servicio: {reserva.servicio}"
            
        except Exception as e:
            print(f"❌ Error cancelando reserva: {e}")
            return "❌ No pude cancelar la reserva. Contacta con el establecimiento."
    
    def _mostrar_info_servicio_detallada(self, args: dict, context: dict) -> str:
        """Mostrar información detallada de servicio informativo"""
        try:
            servicio_id = args["servicio_id"]
            
            servicio_info = next((s for s in context['servicios'] if s['id'] == servicio_id), None)
            if not servicio_info:
                return "❌ Servicio no encontrado."
            
            if not servicio_info['es_informativo']:
                return "❌ Este servicio es para reservas. ¿Quieres ver horarios disponibles?"
            
            mensaje = f"ℹ️ *{servicio_info['nombre']}*\n\n"
            
            if servicio_info['mensaje_personalizado']:
                mensaje += servicio_info['mensaje_personalizado']
            else:
                mensaje += f"Para más información sobre *{servicio_info['nombre']}*, contacta directamente con nosotros."
            
            if context['negocio']['telefono']:
                mensaje += f"\n\n📞 *Teléfono:* {context['negocio']['telefono']}"
            
            mensaje += "\n\n💬 ¿Necesitas algo más? Escribe *\"turno\"* para otros servicios."
            
            return mensaje
            
        except Exception as e:
            print(f"❌ Error mostrando info servicio: {e}")
            return "❌ No pude obtener la información de ese servicio."
    
    def _traducir_dia(self, dia_ingles: str) -> str:
        """Traducir día de la semana a español"""
        traduccion = {
            'Monday': 'Lunes',
            'Tuesday': 'Martes', 
            'Wednesday': 'Miércoles',
            'Thursday': 'Jueves',
            'Friday': 'Viernes',
            'Saturday': 'Sábado',
            'Sunday': 'Domingo'
        }
        return traduccion.get(dia_ingles, dia_ingles)
    
    # Implementar métodos faltantes para empleados...
    async def _buscar_horarios_empleado_inteligente(self, args: dict, context: dict, telefono: str, db: Session) -> str:
        """Buscar horarios con empleado específico"""
        # Similar a _buscar_horarios_servicio_inteligente pero usando empleado
        return "Funcionalidad de empleados en desarrollo..."
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

INSTRUCCIONES:
1. Sé natural, amigable y personalizada
2. Usa la información del cliente para personalizar respuestas
3. Cuando te pidan un turno, pregunta específicamente qué servicio quiere
4. Si dicen un número (1, 2, etc), interpreta que se refiere al servicio de esa posición
5. Ofrece horarios específicos cuando sea apropiado
6. Recuerda conversaciones anteriores
7. Puedes responder preguntas generales sobre el negocio

SERVICIOS DISPONIBLES:
{self._format_servicios_for_ai(business_context['servicios'])}

FUNCIONES DISPONIBLES:
- buscar_horarios_servicio: Para mostrar horarios disponibles
- crear_reserva: Para confirmar una reserva
- cancelar_reserva: Para cancelar reservas existentes
- mostrar_info_servicio: Para servicios informativos
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
                        "servicio_id": {"type": "integer", "description": "ID del servicio"},
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
                        "servicio_id": {"type": "integer"},
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
                model="gpt-4",
                messages=messages,
                functions=functions,
                function_call="auto",
                temperature=0.7,
                max_tokens=1000
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
    
    def _format_servicios_for_ai(self, servicios):
        """Formatear servicios para el prompt de IA"""
        formatted = ""
        for i, servicio in enumerate(servicios, 1):
            formatted += f"{i}. {servicio['nombre']} - {servicio['duracion']} min - ${servicio['precio']}\n"
        return formatted
    
    def _generar_respuesta_fallback(self, mensaje, user_history, business_context):
        """Respuesta de emergencia cuando falla la IA"""
        mensaje_lower = mensaje.lower()
        
        if any(word in mensaje_lower for word in ['turno', 'reserva', 'cita', 'horario']):
            respuesta = "🤖 ¡Hola! Soy la IA asistente. "
            
            if user_history['es_cliente_recurrente']:
                respuesta += f"¡Qué bueno verte de nuevo! "
                if user_history['servicio_favorito']:
                    respuesta += f"Veo que tu servicio favorito es {user_history['servicio_favorito']}. "
            else:
                respuesta += "¡Bienvenido/a! Es la primera vez que te veo por aquí. "
            
            respuesta += "\n\n🎯 *Servicios disponibles:*\n"
            for i, servicio in enumerate(business_context['servicios'], 1):
                respuesta += f"{i}. *{servicio['nombre']}* ({servicio['duracion']} min - ${servicio['precio']})\n"
            
            respuesta += "\n💬 Dime el número del servicio que te interesa o pregúntame lo que necesites."
            return respuesta
        
        return "🤖 ¡Hola! Soy la IA asistente. ¿En qué puedo ayudarte hoy? Puedo ayudarte con reservas, información sobre servicios, o cualquier consulta que tengas."
    
    # ... (mantener el resto de métodos existentes)
    
    def _get_business_context(self, tenant: Tenant, db: Session) -> dict:
        """Obtener contexto completo del negocio"""
        servicios = db.query(Servicio).filter(Servicio.tenant_id == tenant.id).all()
        empleados = db.query(Empleado).filter(Empleado.tenant_id == tenant.id).all()
        
        return {
            "tenant": {
                "id": tenant.id,
                "nombre": tenant.comercio,
                "telefono": tenant.telefono,
                "direccion": tenant.direccion,
                "info": tenant.informacion_local
            },
            "servicios": [
                {
                    "id": s.id,
                    "nombre": s.nombre,
                    "precio": s.precio,
                    "duracion": s.duracion,
                    "es_informativo": s.es_informativo,
                    "mensaje_personalizado": s.mensaje_personalizado
                }
                for s in servicios
            ],
            "empleados": [
                {
                    "id": e.id,
                    "nombre": e.nombre,
                    "calendar_id": e.calendar_id
                }
                for e in empleados
            ]
        }
    
    # ... (implementar métodos faltantes de las funciones)
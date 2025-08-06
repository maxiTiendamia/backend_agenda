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
            preferencia_fecha = args.get("preferencia_fecha", "cualquiera")
            
            servicio = db.query(Servicio).filter(Servicio.id == servicio_id).first()
            if not servicio:
                return "❌ Servicio no encontrado."
            
            # Si el usuario especificó un día específico, aumentar límite de turnos para asegurar que llegue a ese día
            max_turnos = 20 if preferencia_fecha != "cualquiera" else 10
            
            slots = calendar_utils.get_available_slots_for_service(
                servicio,
                intervalo_entre_turnos=getattr(tenant, "intervalo_entre_turnos", 15),
                max_days=7,
                max_turnos=max_turnos,
                credentials_json=self.google_credentials
            )
            
            # Filtrar slots por día específico si se especificó
            if preferencia_fecha and preferencia_fecha != "cualquiera":
                slots_filtrados = []
                hoy = datetime.now(self.tz).date()
                
                if preferencia_fecha == "hoy":
                    fecha_objetivo = hoy
                elif preferencia_fecha == "mañana":
                    fecha_objetivo = hoy + timedelta(days=1)
                elif preferencia_fecha in ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]:
                    # Encontrar el próximo día de la semana especificado
                    dias_semana = {
                        "lunes": 0, "martes": 1, "miércoles": 2, "jueves": 3,
                        "viernes": 4, "sábado": 5, "domingo": 6
                    }
                    dia_objetivo = dias_semana[preferencia_fecha]
                    dias_hasta_objetivo = (dia_objetivo - hoy.weekday()) % 7
                    if dias_hasta_objetivo == 0:  # Si es hoy, tomar el próximo
                        dias_hasta_objetivo = 7
                    fecha_objetivo = hoy + timedelta(days=dias_hasta_objetivo)
                else:
                    fecha_objetivo = None
                
                if fecha_objetivo:
                    slots_filtrados = [slot for slot in slots if slot.date() == fecha_objetivo]
                    slots = slots_filtrados
                    
            if not slots:
                dia_texto = preferencia_fecha if preferencia_fecha != "cualquiera" else "esta semana"
                return f"😔 No hay horarios disponibles para *{servicio.nombre}* {dia_texto}.\n¿Quieres elegir otro día?"
                
            respuesta = f"✨ Horarios disponibles para *{servicio.nombre}*"
            if preferencia_fecha and preferencia_fecha != "cualquiera":
                respuesta += f" el {preferencia_fecha}"
            respuesta += ":\n\n"
            
            for i, slot in enumerate(slots[:8], 1):
                dia_nombre = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][slot.weekday()]
                hora_str = slot.strftime('%d/%m %H:%M')
                respuesta += f"{i}. {dia_nombre.title()} {hora_str}\n"
            respuesta += "\n💬 Escribe el número que prefieres para confirmar."
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
                
                # Crear reserva en la base de datos
                nueva_reserva = Reserva(
                    fake_id=generar_fake_id(),
                    event_id=event_id,
                    empresa=servicio.tenant.comercio,
                    empleado_id=None,
                    empleado_nombre="Sistema",
                    empleado_calendar_id=servicio.calendar_id,
                    cliente_nombre=nombre_cliente,
                    cliente_telefono=telefono,
                    fecha_reserva=slot_dt,
                    servicio=servicio.nombre,
                    estado="activo",
                    cantidad=args.get("cantidad", 1)
                )
                db.add(nueva_reserva)
                db.commit()
                
                return f"✅ Reserva confirmada para {servicio.nombre} el {slot_dt.strftime('%d/%m %H:%M')} a nombre de {nombre_cliente}.\n🔖 Código: {nueva_reserva.fake_id}"
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

    def _get_time_increment(self, tenant):
        """
        Devuelve el incremento de minutos entre turnos según la configuración del Tenant.
        """
        intervalo = getattr(tenant, 'intervalo_entre_turnos', None)
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
        now_aware = datetime.now(self.tz)
        
        # 🔒 SEGURIDAD: Solo reservas del teléfono específico
        # 📅 FILTRADO: Solo reservas futuras (no pasadas)
        reservas_activas = db.query(Reserva).filter(
            Reserva.cliente_telefono == telefono,  # 🔒 Filtro de seguridad por teléfono
            Reserva.estado == "activo",
            Reserva.fecha_reserva > now_aware  # 📅 Solo futuras
        ).order_by(Reserva.fecha_reserva.asc()).all()
        
        reservas_pasadas = db.query(Reserva).filter(
            Reserva.cliente_telefono == telefono,  # 🔒 Filtro de seguridad por teléfono
            Reserva.estado.in_(["completado", "cancelado"])
        ).order_by(Reserva.fecha_reserva.desc()).limit(5).all()
        
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
                codigo_match = re.search(r'\b([A-Z0-9]{6,8})\b', mensaje.upper())  # 🔧 Limitar rango
                if codigo_match:
                    codigo_candidato = codigo_match.group(1)
                    # 🔧 VERIFICAR: Que no sea una palabra común
                    palabras_excluir = [
                        'CANCELAR', 'ANULAR', 'QUIERO', 'HACER', 'RESERVA', 'TURNO'
                    ]
                    if codigo_candidato not in palabras_excluir and re.search(r'\d', codigo_candidato):
                        return await self.cancelar_reserva(codigo_candidato, telefono, db)
                    
                # Si no hay código válido, mostrar reservas
                reservas_activas = user_history.get("reservas_activas", [])
                if not reservas_activas:
                    return "😊 No tienes reservas próximas para cancelar."
                
                respuesta = "🔄 *Tus próximas reservas:*\n\n"
                for r in reservas_activas:
                    if r['puede_cancelar']:
                        respuesta += f"✅ Código: `{r['codigo']}` | {r['servicio']} el {r['fecha']}\n"
                    else:
                        respuesta += f"❌ Código: `{r['codigo']}` | {r['servicio']} el {r['fecha']} _(muy próxima)_\n"
                respuesta += "\n💬 Escribe el código de la reserva que deseas cancelar."
                respuesta += "\n\n_Solo puedes cancelar reservas con más de 1 hora de anticipación._"
                return respuesta

            # --- DETECTAR CÓDIGOS DE RESERVA (sin palabra "cancelar") ---
            # 🔧 MEJORAR: Solo detectar códigos reales, no palabras largas
            codigo_solo = re.search(r'\b([A-Z0-9]{6,8})\b', mensaje.upper())  # Limitar a 6-8 caracteres
            if codigo_solo:
                codigo_candidato = codigo_solo.group(1)
                # 🔧 VERIFICAR: Que no sea una palabra común en español
                palabras_excluir = [
                    'QUIERO', 'HACER', 'RESERVA', 'TURNO', 'HORARIO', 'CANCELAR',
                    'CODIGO', 'TENGO', 'ACTIVOS', 'DISPONIBLE', 'SERVICIO',
                    'MAÑANA', 'TARDE', 'NOCHE', 'VIERNES', 'SABADO', 'DOMINGO'
                ]
                if codigo_candidato not in palabras_excluir:
                    # Verificar que tenga al menos algunos números (códigos reales tienen números)
                    if re.search(r'\d', codigo_candidato):
                        return await self.cancelar_reserva(codigo_candidato, telefono, db)

            # --- CONSULTAR RESERVAS ACTIVAS ---
            if any(phrase in mensaje_stripped for phrase in [
                'turnos activos', 'reservas activas', 'que turnos tengo', 'cuales tengo',
                'mis reservas', 'mis turnos', 'reservas pendientes'
            ]):
                reservas_activas = user_history.get("reservas_activas", [])
                if not reservas_activas:
                    return "😊 No tienes reservas próximas."
                
                respuesta = "📅 *Tus próximas reservas:*\n\n"
                for r in reservas_activas:
                    estado_icono = "✅" if r['puede_cancelar'] else "❌"
                    respuesta += f"{estado_icono} `{r['codigo']}` | {r['servicio']} el {r['fecha']}\n"
                respuesta += "\n💬 Para cancelar, envía el código (ej: `C2HHOH`) o escribe 'cancelar + código'."
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

    def _detectar_hora_mensaje(self, mensaje: str) -> str:
        """🔧 DETECTAR: Hora en diferentes formatos"""
        mensaje = mensaje.lower().strip()
        
        # Patrones de hora más flexibles
        import re
        
        # Formato HH:MM exacto
        time_pattern = r'\b(\d{1,2}):(\d{2})\b'
        time_match = re.search(time_pattern, mensaje)
        if time_match:
            return f"{time_match.group(1).zfill(2)}:{time_match.group(2)}"
        
        # Formato "a las X" o "las X"
        hour_pattern = r'(?:a\s+las\s+|las\s+)(\d{1,2})(?:\s*h|:00|$|\s)'
        hour_match = re.search(hour_pattern, mensaje)
        if hour_match:
            hora = int(hour_match.group(1))
            return f"{hora:02d}:00"
        
        # Formato simple "X de la mañana/tarde"
        simple_pattern = r'\b(\d{1,2})\s*(?:de\s*la\s*)?(?:mañana|tarde|noche)?\b'
        simple_match = re.search(simple_pattern, mensaje)
        if simple_match:
            hora = int(simple_match.group(1))
            # Convertir a formato 24h si es necesario
            if hora <= 12 and ('tarde' in mensaje or 'noche' in mensaje):
                if hora != 12:
                    hora += 12
            return f"{hora:02d}:00"
        
        return None

    def _detectar_cambio_horario(self, mensaje: str) -> bool:
        """🔧 DETECTAR: Si el usuario quiere cambiar de horario"""
        mensaje = mensaje.lower()
        
        # ❌ EXCLUIR frases que NO son cambios de horario
        exclusiones = [
            'mi nombre es', 'me llamo', 'soy ', 'nombre:'
        ]
        if any(exclusion in mensaje for exclusion in exclusiones):
            return False
        
        # ✅ DETECTAR palabras de cambio solo si contienen referencia horaria
        cambio_palabras = [
            'no', 'cambiar', 'otro', 'diferente', 'mejor', 'prefiero',
            'quiero', 'no me gusta', 'no me sirve', 'no puedo'
        ]
        
        # Solo es cambio si menciona horario/tiempo Y tiene palabra de cambio
        tiene_cambio = any(palabra in mensaje for palabra in cambio_palabras)
        tiene_horario = any(palabra in mensaje for palabra in ['hora', 'las ', 'a las', 'de la', 'turno', 'horario'])
        
        return tiene_cambio and tiene_horario

    def _es_nombre_valido(self, mensaje: str) -> bool:
        """🔧 VALIDAR: Si el mensaje contiene un nombre válido"""
        mensaje = mensaje.lower().strip()
        
        # Patrones de nombre válido
        patrones_nombre = [
            'mi nombre es', 'me llamo', 'soy ', 'nombre:'
        ]
        
        # Si contiene patrón de presentación, es nombre válido
        if any(patron in mensaje for patron in patrones_nombre):
            return True
        
        # Si tiene 2+ palabras Y son palabras reales (no solo una palabra)
        palabras = mensaje.split()
        if len(palabras) >= 2:
            # Verificar que no sean referencias de horario/cambio
            referencias_horario = ['hora', 'turno', 'horario', 'las ', 'a las', 'de la']
            if not any(ref in mensaje for ref in referencias_horario):
                # Verificar que cada palabra tenga al menos 2 caracteres
                if all(len(palabra) >= 2 for palabra in palabras):
                    return True
        
        # Si es una sola palabra, debe tener al menos 4 caracteres para ser nombre válido
        if len(palabras) == 1 and len(palabras[0]) >= 4:
            # Verificar que no sea una referencia técnica
            referencias_no_nombre = ['cancelar', 'reservar', 'turno', 'codigo']
            if not any(ref in mensaje for ref in referencias_no_nombre):
                return False  # 🔧 Forzar nombres de 2+ palabras por seguridad
        
        return False

    def _extraer_nombre(self, mensaje: str) -> str:
        """🔧 EXTRAER: El nombre limpio del mensaje"""
        mensaje = mensaje.strip()
        mensaje_lower = mensaje.lower()
        
        # Remover patrones de presentación
        patrones = [
            'mi nombre es ', 'me llamo ', 'soy ', 'nombre: ', 'nombre '
        ]
        
        for patron in patrones:
            if patron in mensaje_lower:
                # Encontrar la posición del patrón y extraer lo que sigue
                idx = mensaje_lower.find(patron)
                return mensaje[idx + len(patron):].strip()
        
        # Si no hay patrón, devolver el mensaje completo
        return mensaje

    def _detectar_dia_mensaje(self, mensaje: str) -> str:
        """🔧 CORREGIDO: Detectar qué día quiere el usuario"""
        mensaje_original = mensaje.lower().strip()
        
        # 🔧 MEJOR LÓGICA: Buscar patrones específicos sin modificar el mensaje globalmente
        if any(word in mensaje_original for word in ['hoy', 'today']):
            return 'hoy'
        elif any(word in mensaje_original for word in ['mañana', 'tomorrow']):
            return 'mañana'
        elif any(word in mensaje_original for word in ['lunes', 'monday']):
            return 'lunes'
        elif any(word in mensaje_original for word in ['martes', 'tuesday']):
            return 'martes'
        elif any(word in mensaje_original for word in ['miércoles', 'miercoles', 'wednesday']):
            return 'miercoles'
        elif any(word in mensaje_original for word in ['jueves', 'thursday']):
            return 'jueves'
        elif any(word in mensaje_original for word in ['viernes', 'vienres', 'friday']):  # 🔧 CORREGIR typo común
            return 'viernes'
        elif any(word in mensaje_original for word in ['sábado', 'sabado', 'saturday']):
            return 'sabado'
        elif any(word in mensaje_original for word in ['domingo', 'sunday']):
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
                # 2. Selección de horario por hora (formatos flexibles)
                hora_detectada = self._detectar_hora_mensaje(mensaje_stripped)
                if hora_detectada:
                    for slot in slots_data:
                        slot_hora = datetime.fromisoformat(slot['fecha_hora']).strftime('%H:%M')
                        if slot_hora == hora_detectada:
                            self.redis_client.set(f"slot_seleccionado:{telefono}", json.dumps(slot), ex=600)
                            return (
                                f"✅ Elegiste:\n\n🎾 *{servicio_guardado['nombre']}*"
                                f"\n📅 {datetime.fromisoformat(slot['fecha_hora']).strftime('%A %d/%m a las %H:%M')}"
                                "\n\n👤 Para confirmar, por favor escribe tu *nombre completo*."
                            )
                    return f"❌ No encontré el horario {hora_detectada}. Elige uno de los horarios numerados."
                # 3. Confirmación de reserva O cambio de horario
                slot_seleccionado_str = self.redis_client.get(f"slot_seleccionado:{telefono}")
                if slot_seleccionado_str:
                    # 🔧 DETECTAR si quiere cambiar de horario
                    if self._detectar_cambio_horario(mensaje_stripped):
                        # El usuario quiere cambiar, buscar nueva hora
                        hora_nueva = self._detectar_hora_mensaje(mensaje_stripped)
                        if hora_nueva:
                            # Buscar el nuevo horario
                            for slot in slots_data:
                                slot_hora = datetime.fromisoformat(slot['fecha_hora']).strftime('%H:%M')
                                if slot_hora == hora_nueva:
                                    self.redis_client.set(f"slot_seleccionado:{telefono}", json.dumps(slot), ex=600)
                                    return (
                                        f"✅ ¡Perfecto! Cambié tu selección:\n\n🎾 *{servicio_guardado['nombre']}*"
                                        f"\n📅 {datetime.fromisoformat(slot['fecha_hora']).strftime('%A %d/%m a las %H:%M')}"
                                        "\n\n👤 Para confirmar, por favor escribe tu *nombre completo*."
                                    )
                            return f"❌ No encontré el horario {hora_nueva}. Los horarios disponibles son:\n" + "\n".join([f"{i}. {datetime.fromisoformat(s['fecha_hora']).strftime('%H:%M')}" for i, s in enumerate(slots_data, 1)])
                        else:
                            # Quiere cambiar pero no especificó hora nueva
                            self.redis_client.delete(f"slot_seleccionado:{telefono}")
                            return f"🔄 ¡Entendido! Te muestro los horarios disponibles otra vez:\n\n" + "\n".join([f"{i}. {datetime.fromisoformat(s['fecha_hora']).strftime('%H:%M')}" for i, s in enumerate(slots_data, 1)]) + "\n\n💬 Escribe el número o la hora que prefieres."
                    
                    # 🔧 CONFIRMACIÓN: Solo si parece un nombre (más de 2 palabras o no contiene cambios)
                    elif self._es_nombre_valido(mensaje_stripped):
                        slot_seleccionado = json.loads(slot_seleccionado_str)
                        # Extraer el nombre limpio
                        nombre_cliente = self._extraer_nombre(mensaje.strip())
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
                            
                            # Crear reserva en la base de datos
                            nueva_reserva = Reserva(
                                fake_id=generar_fake_id(),
                                event_id=event_id,
                                empresa=servicio_modelo.tenant.comercio,
                                empleado_id=None,
                                empleado_nombre="Sistema",
                                empleado_calendar_id=servicio_modelo.calendar_id,
                                cliente_nombre=nombre_cliente,
                                cliente_telefono=telefono,
                                fecha_reserva=slot_dt,
                                servicio=servicio_modelo.nombre,
                                estado="activo",
                                cantidad=1
                            )
                            db.add(nueva_reserva)
                            db.commit()
                            
                            # Limpiar selección en Redis
                            self.redis_client.delete(f"servicio_seleccionado:{telefono}")
                            self.redis_client.delete(f"slots:{telefono}:{servicio_guardado['id']}")
                            self.redis_client.delete(f"slot_seleccionado:{telefono}")
                            return (
                                f"✅ Reserva confirmada para *{servicio_guardado['nombre']}*"
                                f"\n📅 {slot_dt.strftime('%A %d/%m %H:%M')}"
                                f"\n👤 A nombre de: {nombre_cliente}"
                                f"\n🔖 Código: {nueva_reserva.fake_id}"
                                f"\n\n¡Gracias por reservar! 😊"
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
                    intervalo_entre_turnos=getattr(tenant, "intervalo_entre_turnos", 15),
                    max_days=7,
                    max_turnos=25,  # 🔧 AUMENTAR para asegurar que llegue al día específico
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
                    # Normalizar nombre del día (quitar acentos)
                    dia_normalizado = dia_detectado.replace("é", "e").replace("á", "a")
                    try:
                        idx = dias_semana.index(dia_normalizado)
                        hoy_idx = now.weekday()
                        dias_hasta = (idx - hoy_idx) % 7
                        if dias_hasta == 0:  # Si es hoy, tomar el próximo de esa semana
                            dias_hasta = 7
                        dia_objetivo = (now + timedelta(days=dias_hasta)).date()
                        print(f"🔧 DEBUG: Día detectado: {dia_detectado}, Normalizado: {dia_normalizado}, Hoy: {now.strftime('%A %d/%m')}, Objetivo: {dia_objetivo.strftime('%A %d/%m')}")
                    except ValueError:
                        print(f"❌ Error: día '{dia_detectado}' no reconocido")
                        return f"❌ No reconozco el día '{dia_detectado}'. Usa: hoy, mañana, lunes, martes, etc."
                slots_dia = [s for s in slots if s.date() == dia_objetivo]
                if not slots_dia:
                    return f"😔 No hay horarios disponibles para *{servicio_guardado_dict['nombre']}* el {dia_detectado}.\n¿Quieres elegir otro día?"
                # Guardar slots en Redis
                slots_key = f"slots:{telefono}:{servicio_guardado_dict['id']}"
                slots_data = [
                    {
                        "numero": i,
                        "fecha_hora": slot.isoformat(),
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
8. 📅 IMPORTANTE: Si el usuario menciona un día específico (hoy, mañana, lunes, martes, etc.), usa ese día exacto en el parámetro preferencia_fecha
9. 🚫 NO busques horarios cuando pregunten por sus reservas actuales o códigos de cancelación
10. 💬 Si preguntan por turnos activos/reservas, indica que pueden cancelar enviando solo el código

🛠️ FUNCIONES DISPONIBLES:
- 🔍 buscar_horarios_servicio: Para mostrar horarios disponibles (usa el ID real del servicio y preferencia_fecha si el usuario especifica un día)
- ❌ cancelar_reserva: Para cancelar reservas existentes

⚠️ IMPORTANTE: NO puedes crear reservas directamente. El flujo de reserva se maneja automáticamente cuando el usuario selecciona horario y proporciona su nombre.

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
        
        # Definir funciones disponibles - SOLO buscar horarios, NO crear reservas
        functions = [
            {
                "name": "buscar_horarios_servicio",
                "description": "Buscar horarios disponibles para un servicio específico",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "servicio_id": {"type": "integer", "description": "ID REAL del servicio en la base de datos"},
                        "preferencia_horario": {"type": "string", "description": "mañana, tarde, noche o cualquiera"},
                        "preferencia_fecha": {"type": "string", "description": "hoy, mañana, lunes, martes, miércoles, jueves, viernes, sábado, domingo, esta_semana o cualquiera"},
                        "cantidad": {"type": "integer", "description": "Cantidad de personas", "default": 1}
                    },
                    "required": ["servicio_id"]
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
                "mensaje_personalizado": getattr(s, "mensaje_personalizado", "")
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

    async def cancelar_reserva(self, codigo_reserva: str, telefono: str, db: Session):
        """Cancelar una reserva por código"""
        try:
            # 🔒 SEGURIDAD REFORZADA: Buscar la reserva con múltiples filtros de seguridad
            now_aware = datetime.now(self.tz)
            
            reserva = db.query(Reserva).filter(
                Reserva.fake_id == codigo_reserva,  # Código específico
                Reserva.cliente_telefono == telefono,  # 🔒 Solo del teléfono del usuario
                Reserva.estado == "activo",  # Solo activas
                Reserva.fecha_reserva > now_aware  # 📅 Solo futuras
            ).first()
            
            if not reserva:
                return f"❌ No encontré la reserva con código `{codigo_reserva}` o no se puede cancelar.\n\n_Verifica que el código sea correcto y que la reserva sea futura._"
            
            # 🔒 VERIFICACIÓN ADICIONAL: Confirmar que es del mismo teléfono
            if reserva.cliente_telefono != telefono:
                print(f"🚨 INTENTO DE ACCESO NO AUTORIZADO: {telefono} intentó cancelar reserva de {reserva.cliente_telefono}")
                return "❌ No tienes autorización para cancelar esta reserva."
            
            # Verificar si se puede cancelar (debe ser con al menos 1 hora de anticipación)
            if not self._puede_cancelar_reserva(reserva.fecha_reserva, now_aware):
                tiempo_restante = (self._normalize_datetime(reserva.fecha_reserva) - now_aware).total_seconds() / 60
                return f"❌ No puedes cancelar reservas con menos de 1 hora de anticipación.\n\n_Tu reserva es en {int(tiempo_restante)} minutos._"
            
            # Intentar cancelar en Google Calendar si existe
            if reserva.event_id:
                from api.utils import calendar_utils
                # Usar el empleado_calendar_id que ya está guardado en la reserva
                calendar_utils.cancelar_evento_google(
                    reserva.empleado_calendar_id,
                    reserva.event_id,
                    self.google_credentials
                )
            
            # Actualizar estado en la base de datos
            reserva.estado = "cancelado"
            db.commit()
            
            return f"✅ *Reserva cancelada correctamente*\n\n📅 {reserva.servicio} el {reserva.fecha_reserva.strftime('%d/%m %H:%M') if reserva.fecha_reserva else ''}\n🔖 Código: `{codigo_reserva}`\n\n😊 ¡Esperamos verte pronto!"
            
        except Exception as e:
            print(f"❌ Error cancelando reserva: {e}")
            return f"❌ Error al cancelar la reserva: {str(e)}"

def _parse_working_hours(wh):
    if wh is None:
        return None
    if isinstance(wh, str):
        try:
            return json.loads(wh)
        except Exception:
            return None
    return wh
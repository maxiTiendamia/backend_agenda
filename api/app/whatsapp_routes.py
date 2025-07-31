from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import json
import time
import os
import re
from dotenv import load_dotenv

# 🔥 CORREGIR IMPORTS - usar imports relativos
from .models import Tenant, Servicio, Empleado, Reserva, ErrorLog, BlockedNumber
from .deps import get_db
from ..utils.calendar_utils import get_available_slots, create_event, cancelar_evento_google, get_available_slots_for_service, create_event_for_service
from ..utils.generador_fake_id import generar_fake_id
import redis
import httpx
import asyncio
from datetime import datetime, timedelta
import pytz  # Asegúrate de que esto esté importado

REDIS_URL = os.getenv("REDIS_URL", "rediss://default:AcOQAAIjcDEzOGI2OWU1MzYxZDQ0YWQ2YWU3ODJlNWNmMGY5MjIzY3AxMA@literate-toucan-50064.upstash.io:6379")
WEBCONNECT_URL = os.getenv("webconnect_url", "http://195.26.250.62:3000")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
SESSION_TTL = 300  # segundos
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)
    
def hay_disponibilidad(db, servicio, slot):
    # Cuenta reservas activas para ese servicio y ese horario
    return db.query(Reserva).filter(
        Reserva.servicio == servicio.nombre,
        Reserva.fecha_reserva == slot,
        Reserva.estado == "activo"
    ).count() < (servicio.cantidad or 1)

def hay_disponibilidad_servicio(db, servicio, slot):
    """Verifica disponibilidad para un servicio específico en un slot dado"""
    return db.query(Reserva).filter(
        Reserva.servicio == servicio.nombre,
        Reserva.fecha_reserva == slot,
        Reserva.estado == "activo"
    ).count() < (servicio.cantidad or 1)

def set_user_state(user_id, state):
    try:
        redis_client.setex(
            f"user_state:{user_id}",
            SESSION_TTL,
            json.dumps(state, cls=DateTimeEncoder)
        )
    except Exception as e:
        print(f"⚠️ Error guardando estado en Redis: {e}")

def get_user_state(user_id):
    try:
        state_json = redis_client.get(f"user_state:{user_id}")
        return json.loads(state_json) if state_json else None
    except Exception as e:
        print(f"⚠️ Error leyendo estado de Redis: {e}")
        return None

def generar_mensaje_bienvenida(tenant):
    """Generar mensaje de bienvenida personalizado con información del cliente"""
    mensaje = f"¡Hola! 👋 Soy el asistente virtual de *{tenant.comercio}*\n\n"
    
    # Agregar información del local si está disponible
    if tenant.informacion_local:
        mensaje += f"ℹ️ *Acerca de nosotros:*\n\n{tenant.informacion_local}\n\n"
    
    # Agregar dirección si está disponible
    if tenant.direccion:
        mensaje += f"📍 *Dirección:* {tenant.direccion}\n\n"
    
    # Agregar teléfono de contacto si está disponible
    if tenant.telefono:
        mensaje += f"📞 *Teléfono:* {tenant.telefono}\n\n"
    
    # Servicios disponibles
    mensaje += "🎯 *¿Qué deseas hacer?*\n\n"
    mensaje += "🔹 Escribe *\"Turno\"* o *\"Reservar\"* para reservar nuestros servicios\n"
    mensaje += "🔹 Escribe *\"Ayuda\"* para hablar con un asesor\n\n"
    
    return mensaje

async def notificar_chat_humano_completo(cliente_id: int, telefono: str, mensaje: str):
    """Registrar solicitud de atención humana (sin autonotificación)"""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{WEBCONNECT_URL}/notificar-chat-humano",
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

router = APIRouter()

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")

@router.post("/webhook")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
        telefono = data.get("telefono")
        mensaje = data.get("mensaje", "").strip().lower()
        cliente_id = data.get("cliente_id")

        # Validar que cliente_id sea un entero
        try:
            cliente_id = int(cliente_id)
        except (TypeError, ValueError):
            return JSONResponse(content={"mensaje": "❌ Error: cliente_id inválido."}, status_code=400)

        tenant = db.query(Tenant).filter_by(id=cliente_id).first()
        if not tenant:
            return JSONResponse(content={"mensaje": "⚠️ Cliente no encontrado."})

        # 🔥 VERIFICACIÓN COMPLETA DE NÚMEROS BLOQUEADOS (ÚNICA)
        numeros_bloqueados = db.query(BlockedNumber).filter(
            (BlockedNumber.telefono == telefono) &
            (BlockedNumber.cliente_id == cliente_id)
        ).all()

        if numeros_bloqueados:
            # Log detallado del bloqueo
            tipos_bloqueo = []
            for bloqueo in numeros_bloqueados:
                if bloqueo.empleado_id is None:
                    tipos_bloqueo.append("nivel_cliente")
                else:
                    tipos_bloqueo.append(f"empleado_{bloqueo.empleado_id}")
            
            print(f"🚫 Número {telefono} bloqueado para cliente {cliente_id} ({', '.join(tipos_bloqueo)}) - No se responderá")
            
            # Opcional: Registrar en log de la base de datos
            try:
                error_log = ErrorLog(
                    cliente=str(cliente_id),
                    telefono=telefono,
                    mensaje=mensaje,
                    error=f"Número bloqueado - {len(numeros_bloqueados)} regla(s) de bloqueo activa(s)"
                )
                db.add(error_log)
                db.commit()
            except Exception as log_error:
                print(f"⚠️ Error guardando log de bloqueo: {log_error}")
            
            return JSONResponse(content={"mensaje": ""}, status_code=200)

        # --- RESTO DEL CÓDIGO CONTINÚA NORMAL ---
        now = time.time()
        state = get_user_state(telefono)
        
        # Si no hay estado previo o es muy antiguo, crear estado inicial
        if not state or now - state.get("last_interaction", 0) > SESSION_TTL:
            state = {"step": "welcome", "last_interaction": now, "mode": "bot", "is_first_contact": True}
        else:
            state["last_interaction"] = now

        # --- MANEJO DE MODO HUMANO ---
        if state.get("mode") == "human":
            if mensaje in ["bot", "volver", "Bot", "VOLVER", "BOT"]:
                state["mode"] = "bot"
                state["step"] = "welcome"
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": "🤖 El asistente virtual está activo nuevamente. Escribe \"Turno\" para agendar."})
            else:
                try:
                    import asyncio
                    asyncio.create_task(notificar_chat_humano_completo(tenant.id, telefono, mensaje))
                except Exception as e:
                    print(f"⚠️ Error enviando notificación: {e}")
                return JSONResponse(content={"mensaje": ""})

        # --- SOLICITUD DE AYUDA ---
        if "ayuda" in mensaje:
            state["mode"] = "human"
            state["step"] = "human_mode"
            set_user_state(telefono, state)
            try:
                import asyncio
                asyncio.create_task(notificar_chat_humano_completo(tenant.id, telefono, mensaje))
            except Exception as e:
                print(f"⚠️ Error enviando notificación: {e}")
            return JSONResponse(content={"mensaje": "🚪 Un asesor te responderá a la brevedad. Puedes escribir \"Bot\" y volveré a ayudarte 😊"})

        # Actualizar estado solo si NO está en modo humano
        set_user_state(telefono, state)

        # --- MENSAJES DE DESPEDIDA ---
        if any(x in mensaje for x in ["gracias", "chau", "chao", "nos vemos"]):
            return JSONResponse(content={"mensaje": "😊 ¡Gracias por tu mensaje! Que tengas un buen día!"})

        # --- CANCELAR RESERVA ---
        if re.match(r"^cancelar\s+\w+", mensaje):
            partes = mensaje.strip().split(maxsplit=1)
            if len(partes) < 2:
                return JSONResponse(content={"mensaje": "❌ Debes escribir: cancelar + código"})
            fake_id = partes[1].strip().upper()
            try:
                reserva = db.query(Reserva).filter_by(fake_id=fake_id, estado="activo").first()
                if not reserva:
                    return JSONResponse(content={"mensaje": "❌ No se encontró la reserva. Verifica el código."})
                
                zona_uy = pytz.timezone("America/Montevideo")
                ahora = datetime.now(zona_uy)
                
                if reserva.fecha_reserva.tzinfo is None:
                    reserva_dt = zona_uy.localize(reserva.fecha_reserva)
                else:
                    reserva_dt = reserva.fecha_reserva.astimezone(zona_uy)
                    
                print("DEBUG cancelar:", reserva_dt, ahora, reserva_dt - ahora)
                    
                if reserva_dt - ahora < timedelta(hours=1):
                    return JSONResponse(content={"mensaje": "⏰ No podés cancelar un turno con menos de 1 hora de anticipación. Contactá al local si necesitás ayuda."})
                
                exito = cancelar_evento_google(
                    calendar_id=reserva.empleado_calendar_id,
                    reserva_id=reserva.event_id,
                    service_account_info=GOOGLE_CREDENTIALS_JSON
                )
                if exito:
                    reserva.estado = "cancelado"
                    db.commit()
                    state.clear()
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": "✅ Tu turno fue cancelado correctamente."})
                else:
                    return JSONResponse(content={"mensaje": "❌ No se pudo cancelar el turno. Intenta más tarde."})
            except Exception as e:
                print("❌ Error al cancelar turno:", e)
                return JSONResponse(content={"mensaje": "❌ No se pudo cancelar el turno. Intenta más tarde."})

        # --- FLUJO PRINCIPAL ---
        if state.get("step") == "welcome":
            if state.get("is_first_contact"):
                state["is_first_contact"] = False
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": generar_mensaje_bienvenida(tenant)})

            if "turno" in mensaje or "reservar" in mensaje or "agendar" in mensaje:
                servicios = tenant.servicios
                empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                
                # 🆕 NUEVO: Si hay servicios con calendario configurado
                servicios_con_calendario = [s for s in servicios if s.calendar_id and s.working_hours]
                
                if servicios_con_calendario:
                    msg = "¿Qué servicio deseas reservar?\n"
                    for i, s in enumerate(servicios_con_calendario, 1):
                        msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                    msg += "\nResponde con el número del servicio."
                    state["step"] = "waiting_servicio_con_calendario"
                    state["servicios"] = [s.id for s in servicios_con_calendario]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                
                # Si NO hay servicios con calendario pero sí empleados
                elif empleados:
                    msg = "¿Con qué empleado deseas reservar?\n"
                    for i, e in enumerate(empleados, 1):
                        msg += f"🔹{i}. {e.nombre}\n"
                    msg += "\nResponde con el número del empleado."
                    state["step"] = "waiting_empleado_sin_servicio"
                    state["empleados"] = [e.id for e in empleados]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                
                # 🔥 ELIMINAR lógica de calendar_id_general
                else:
                    return JSONResponse(content={"mensaje": "⚠️ No hay servicios disponibles para reservar turnos en este momento. Contacta con el establecimiento para más información."})

        # 🆕 NUEVO: Manejo de servicios con calendario propio
        if state.get("step") == "waiting_servicio_con_calendario":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                servicios_ids = state.get("servicios", [])
                if 0 <= idx < len(servicios_ids):
                    servicio_id = servicios_ids[idx]
                    servicio = db.query(Servicio).get(servicio_id)
                    
                    if not servicio.calendar_id or not servicio.working_hours:
                        return JSONResponse(content={"mensaje": f"❌ El servicio {servicio.nombre} no está configurado correctamente. Contacta con el establecimiento."})
                    
                    # Usar la nueva función para obtener slots del servicio
                    slots = get_available_slots_for_service(
                        servicio=servicio,
                        intervalo_entre_turnos=tenant.intervalo_entre_turnos or 20,
                        max_turnos=25,
                        credentials_json=GOOGLE_CREDENTIALS_JSON
                    )
                    
                    ahora = datetime.now(pytz.timezone("America/Montevideo"))
                    slots_futuros = [s for s in slots if s > ahora]
                    # Filtrar según disponibilidad
                    slots_disponibles = [s for s in slots_futuros if hay_disponibilidad_servicio(db, servicio, s)]
                    
                    if not slots_disponibles:
                        return JSONResponse(content={"mensaje": f"⚠️ No hay turnos disponibles para {servicio.nombre} en este momento."})
                    
                    msg = f"📅 Turnos disponibles para {servicio.nombre}:\n"
                    for i, slot in enumerate(slots_disponibles[:25], 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    
                    state["step"] = "waiting_turno_servicio"
                    state["servicio_id"] = servicio_id
                    state["slots"] = [s.isoformat() for s in slots_disponibles[:25]]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                else:
                    servicios_con_calendario = [db.query(Servicio).get(sid) for sid in state.get("servicios", [])]
                    msg = "❌ Opción inválida.\n¿Qué servicio deseas reservar?\n"
                    for i, s in enumerate(servicios_con_calendario, 1):
                        msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                    msg += "\nResponde con el número del servicio."
                    return JSONResponse(content={"mensaje": msg})
            else:
                servicios_con_calendario = [db.query(Servicio).get(sid) for sid in state.get("servicios", [])]
                msg = "❌ Opción inválida.\n¿Qué servicio deseas reservar?\n"
                for i, s in enumerate(servicios_con_calendario, 1):
                    msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                msg += "\nResponde con el número del servicio."
                return JSONResponse(content={"mensaje": msg})

        # 🆕 NUEVO: Selección de turno para servicio con calendario
        if state.get("step") == "waiting_turno_servicio":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                if 0 <= idx < len(slots):
                    slot = slots[idx]
                    state["slot"] = slot.isoformat()
                    state["step"] = "waiting_nombre_servicio"
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": "Por favor, escribe tu nombre y apellido para confirmar la reserva."})
                else:
                    slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                    msg = "❌ Opción inválida.\n📅 Estos son los turnos disponibles:\n"
                    for i, slot in enumerate(slots, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    return JSONResponse(content={"mensaje": msg})
            else:
                slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                msg = "❌ Opción inválida.\n📅 Estos son los turnos disponibles:\n"
                for i, slot in enumerate(slots, 1):
                    msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                return JSONResponse(content={"mensaje": msg})

        # 🆕 NUEVO: Confirmación de nombre para servicio con calendario
        if state.get("step") == "waiting_nombre_servicio":
            nombre_apellido = mensaje.strip().title()
            slot = state.get("slot")
            if isinstance(slot, str):
                slot = datetime.fromisoformat(slot)
            
            servicio = db.query(Servicio).get(state["servicio_id"])
            
            # Verificar disponibilidad una vez más
            if not hay_disponibilidad_servicio(db, servicio, slot):
                # Recalcular slots disponibles
                slots_actuales = get_available_slots_for_service(
                    servicio=servicio,
                    intervalo_entre_turnos=tenant.intervalo_entre_turnos or 20,
                    max_turnos=10,
                    credentials_json=GOOGLE_CREDENTIALS_JSON
                )
                ahora = datetime.now(pytz.timezone("America/Montevideo"))
                slots_futuros = [s for s in slots_actuales if s > ahora]
                slots_disponibles = [s for s in slots_futuros if hay_disponibilidad_servicio(db, servicio, s)]
                
                msg = "❌ El turno seleccionado ya no está disponible. Por favor, elige otro:\n"
                for i, s in enumerate(slots_disponibles[:10], 1):
                    msg += f"🔹{i}. {s.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                
                state["step"] = "waiting_turno_servicio"
                state["slots"] = [s.isoformat() for s in slots_disponibles[:10]]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})

            # Crear evento en Google Calendar del servicio
            try:
                event_id = create_event_for_service(
                    servicio=servicio,
                    slot_dt=slot,
                    user_phone=telefono,
                    service_account_info=GOOGLE_CREDENTIALS_JSON,
                    client_name=nombre_apellido
                )
                
                fake_id = generar_fake_id()
                reserva = Reserva(
                    fake_id=fake_id,
                    event_id=event_id,
                    empresa=tenant.comercio,
                    empleado_id=None,
                    empleado_nombre="(Servicio directo)",
                    empleado_calendar_id=servicio.calendar_id,
                    cliente_nombre=nombre_apellido,
                    cliente_telefono=telefono,
                    fecha_reserva=slot,
                    servicio=servicio.nombre,
                    estado="activo"
                )
                db.add(reserva)
                db.commit()
                
                state.clear()
                set_user_state(telefono, state)
                
                return JSONResponse(content={"mensaje": (
                    f"✅ {nombre_apellido}, tu turno fue reservado con éxito para el {slot.strftime('%d/%m %H:%M')}.\n"
                    f"\nServicio: {servicio.nombre} ({servicio.duracion} min)\n"
                    f"Precio: ${servicio.precio}\n"
                    f"Dirección: {tenant.direccion or '📍 a confirmar con el asesor'}\n"
                    f"\nSi necesitas cancelar, escribe: cancelar {fake_id}"
                )})
                
            except Exception as e:
                print(f"❌ Error creando reserva para servicio: {e}")
                return JSONResponse(content={"mensaje": "❌ Error al crear la reserva. Por favor, intenta nuevamente."})

        # 🔥 ELIMINAR todos los pasos relacionados con calendar_id_general:
        # - waiting_turno_final_general 
        # - waiting_nombre_general
        # Y actualizar la lógica de waiting_servicio para usar solo empleados si no hay servicios con calendario

        # 🔥 ACTUALIZAR waiting_servicio para manejar solo empleados
        if state.get("step") == "waiting_servicio":
            # Esta lógica ahora solo se ejecuta cuando hay servicios SIN calendario y empleados disponibles
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                servicios_ids = state.get("servicios", [])
                if 0 <= idx < len(servicios_ids):
                    servicio_id = servicios_ids[idx]
                    servicio = db.query(Servicio).get(servicio_id)
                    empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                    
                    if not empleados:
                        return JSONResponse(content={"mensaje": "⚠️ No hay empleados disponibles para este servicio."})
                    
                    msg = "¿Con qué empleado?\n"
                    for i, e in enumerate(empleados, 1):
                        msg += f"🔹{i}. {e.nombre}\n"
                    msg += "\nResponde con el número del empleado."
                    
                    state["step"] = "waiting_empleado"
                    state["servicio_id"] = servicio_id  # 🆕 AGREGAR ESTA LÍNEA
                    state["empleados"] = [e.id for e in empleados]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                else:
                    # Mostrar servicios nuevamente
                    servicios = [db.query(Servicio).get(sid) for sid in servicios_ids]
                    msg = "❌ Opción inválida.\n¿Qué servicio deseas reservar?\n"
                    for i, s in enumerate(servicios, 1):
                        msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                    msg += "\nResponde con el número del servicio."
                    return JSONResponse(content={"mensaje": msg})
            else:
                servicios = [db.query(Servicio).get(sid) for sid in state.get("servicios", [])]
                msg = "❌ Opción inválida.\n¿Qué servicio deseas reservar?\n"
                for i, s in enumerate(servicios, 1):
                    msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                msg += "\nResponde con el número del servicio."
                return JSONResponse(content={"mensaje": msg})

        # 🆕 AGREGAR nuevo manejo para waiting_empleado_sin_servicio
        if state.get("step") == "waiting_empleado_sin_servicio":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                empleados_ids = state.get("empleados", [])
                if 0 <= idx < len(empleados_ids):
                    empleado_id = empleados_ids[idx]
                    empleado = db.query(Empleado).get(empleado_id)
                    
                    if not empleado.calendar_id or not empleado.working_hours:
                        return JSONResponse(content={"mensaje": f"❌ El empleado {empleado.nombre} no está configurado correctamente. Contacta con el establecimiento."})
                    
                    # Si hay servicios disponibles, mostrarlos
                    servicios = tenant.servicios
                    if servicios:
                        msg = "¿Qué servicio deseas reservar?\n"
                        for i, s in enumerate(servicios, 1):
                            msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                        msg += "\nResponde con el número del servicio."
                        
                        state["step"] = "waiting_servicio"
                        state["empleado_id"] = empleado_id
                        state["servicios"] = [s.id for s in servicios]
                        set_user_state(telefono, state)
                        return JSONResponse(content={"mensaje": msg})
                    else:
                        return JSONResponse(content={"mensaje": "⚠️ No hay servicios configurados para este empleado."})
                else:
                    empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                    msg = "❌ Opción inválida.\n¿Con qué empleado deseas reservar?\n"
                    for i, e in enumerate(empleados, 1):
                        msg += f"🔹{i}. {e.nombre}\n"
                    msg += "\nResponde con el número del empleado."
                    return JSONResponse(content={"mensaje": msg})
            else:
                empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                msg = "❌ Opción inválida.\n¿Con qué empleado deseas reservar?\n"
                for i, e in enumerate(empleados, 1):
                    msg += f"🔹{i}. {e.nombre}\n"
                msg += "\nResponde con el número del empleado."
                return JSONResponse(content={"mensaje": msg})

        # 🔥 FALTA MANEJO DEL PASO waiting_empleado
        if state.get("step") == "waiting_empleado":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                empleados_ids = state.get("empleados", [])
                if 0 <= idx < len(empleados_ids):
                    empleado_id = empleados_ids[idx]
                    empleado = db.query(Empleado).get(empleado_id)
                    servicio = db.query(Servicio).get(state["servicio_id"])
                    
                    if not empleado.calendar_id or not empleado.working_hours:
                        return JSONResponse(content={"mensaje": f"❌ El empleado {empleado.nombre} no está configurado correctamente. Contacta con el establecimiento."})
                    
                    # Obtener slots disponibles del empleado
                    slots = get_available_slots(
                        calendar_id=empleado.calendar_id,
                        credentials_json=GOOGLE_CREDENTIALS_JSON,
                        working_hours_json=empleado.working_hours,
                        service_duration=servicio.duracion,
                        intervalo_entre_turnos=tenant.intervalo_entre_turnos or 20,
                        max_turnos=25,
                        cantidad=servicio.cantidad or 1,
                        solo_horas_exactas=servicio.solo_horas_exactas or False
                    )
                    
                    ahora = datetime.now(pytz.timezone("America/Montevideo"))
                    slots_futuros = [s for s in slots if s > ahora]
                    slots_disponibles = [s for s in slots_futuros if hay_disponibilidad_servicio(db, servicio, s)]
                    
                    if not slots_disponibles:
                        return JSONResponse(content={"mensaje": f"⚠️ No hay turnos disponibles para {empleado.nombre} en este momento."})
                    
                    msg = f"📅 Turnos disponibles con {empleado.nombre} para {servicio.nombre}:\n"
                    for i, slot in enumerate(slots_disponibles[:25], 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    
                    state["step"] = "waiting_turno_final"
                    state["empleado_id"] = empleado_id
                    state["slots"] = [s.isoformat() for s in slots_disponibles[:25]]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                else:
                    empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                    msg = "❌ Opción inválida.\n¿Con qué empleado?\n"
                    for i, e in enumerate(empleados, 1):
                        msg += f"🔹{i}. {e.nombre}\n"
                    msg += "\nResponde con el número del empleado."
                    return JSONResponse(content={"mensaje": msg})
            else:
                empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                msg = "❌ Opción inválida.\n¿Con qué empleado?\n"
                for i, e in enumerate(empleados, 1):
                    msg += f"🔹{i}. {e.nombre}\n"
                msg += "\nResponde con el número del empleado."
                return JSONResponse(content={"mensaje": msg})

        # 🚨 FALTAN ESTOS PASOS CRÍTICOS:

        # waiting_turno_final - Para empleados
        if state.get("step") == "waiting_turno_final":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                if 0 <= idx < len(slots):
                    slot = slots[idx]
                    state["slot"] = slot.isoformat()
                    state["step"] = "waiting_nombre_empleado"
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": "Por favor, escribe tu nombre y apellido para confirmar la reserva."})
                else:
                    slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                    msg = "❌ Opción inválida.\n📅 Estos son los turnos disponibles:\n"
                    for i, slot in enumerate(slots, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    return JSONResponse(content={"mensaje": msg})
            else:
                slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                msg = "❌ Opción inválida.\n📅 Estos son los turnos disponibles:\n"
                for i, slot in enumerate(slots, 1):
                    msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                return JSONResponse(content={"mensaje": msg})

        # waiting_nombre_empleado - Confirmación final para empleados  
        if state.get("step") == "waiting_nombre_empleado":
            nombre_apellido = mensaje.strip().title()
            slot = state.get("slot")
            if isinstance(slot, str):
                slot = datetime.fromisoformat(slot)
            
            empleado = db.query(Empleado).get(state["empleado_id"])
            servicio = db.query(Servicio).get(state["servicio_id"])
            
            # Verificar disponibilidad una vez más
            if not hay_disponibilidad_servicio(db, servicio, slot):
                return JSONResponse(content={"mensaje": "❌ El turno seleccionado ya no está disponible. Escribe 'Turno' para ver nuevas opciones."})
            
            # Crear evento en Google Calendar del empleado
            try:
                event_id = create_event(
                    calendar_id=empleado.calendar_id,
                    slot_dt=slot,
                    user_phone=telefono,
                    service_account_info=GOOGLE_CREDENTIALS_JSON,
                    duration_minutes=servicio.duracion,
                    client_service=f"{nombre_apellido} - {servicio.nombre}"
                )
                
                fake_id = generar_fake_id()
                reserva = Reserva(
                    fake_id=fake_id,
                    event_id=event_id,
                    empresa=tenant.comercio,
                    empleado_id=empleado.id,
                    empleado_nombre=empleado.nombre,
                    empleado_calendar_id=empleado.calendar_id,
                    cliente_nombre=nombre_apellido,
                    cliente_telefono=telefono,
                    fecha_reserva=slot,
                    servicio=servicio.nombre,
                    estado="activo"
                )
                db.add(reserva)
                db.commit()
                
                state.clear()
                set_user_state(telefono, state)
                
                return JSONResponse(content={"mensaje": (
                    f"✅ {nombre_apellido}, tu turno fue reservado con éxito para el {slot.strftime('%d/%m %H:%M')}.\n"
                    f"\nEmpleado: {empleado.nombre}\n"
                    f"Servicio: {servicio.nombre} ({servicio.duracion} min)\n"
                    f"Precio: ${servicio.precio}\n"
                    f"Dirección: {tenant.direccion or '📍 a confirmar con el asesor'}\n"
                    f"\nSi necesitas cancelar, escribe: cancelar {fake_id}"
                )})
                
            except Exception as e:
                print(f"❌ Error creando reserva con empleado: {e}")
                return JSONResponse(content={"mensaje": "❌ Error al crear la reserva. Por favor, intenta nuevamente."})

        # 🔥 REMOVER COMENTARIOS INNECESARIOS Y DUPLICACIONES

        # Manejo por defecto para mensajes no reconocidos
        return JSONResponse(content={"mensaje": (
            "🤔 No entendí tu mensaje.\n\n"
            "¿Qué deseas hacer?\n"
            "🔹 Escribe \"Turno\" para agendar\n"
            "🔹 Escribe \"Ayuda\" para hablar con un asesor"
        )})

    except Exception as e:
        import traceback as tb
        error_text = tb.format_exc()
        
        # Usar variables seguras para evitar errores
        cliente_info = tenant.comercio if 'tenant' in locals() and tenant else "Desconocido"
        telefono_info = telefono if 'telefono' in locals() else "Desconocido"
        mensaje_info = mensaje if 'mensaje' in locals() else "Desconocido"
        
        log = ErrorLog(
            cliente=cliente_info,
            telefono=telefono_info,
            mensaje=mensaje_info,
            error=error_text
        )
        db.add(log)
        db.commit()
        
        print("❌ Error general procesando mensaje:", e)
        
        # Reiniciar el estado para que el usuario pueda seguir interactuando
        if 'telefono' in locals():
            state = {"step": "welcome", "last_interaction": time.time(), "mode": "bot", "is_first_contact": False}
            set_user_state(telefono, state)
        
        return JSONResponse(content={
            "mensaje": (
                "❌ Ocurrió un error inesperado. Volvé a intentar tu reserva.\n\n"
                "¿Qué deseas hacer?\n"
                "🔹 Escribe \"Turno\" para agendar\n"
                "🔹 Escribe \"Ayuda\" para hablar con un asesor"
            )
        })



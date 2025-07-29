from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from api.app.models import Tenant, Servicio, Empleado, Reserva, ErrorLog, BlockedNumber
from api.app.deps import get_db
from api.utils.calendar_utils import get_available_slots, create_event, cancelar_evento_google
from api.utils.generador_fake_id import generar_fake_id
import time
import re
import os
import pytz
import redis
import json
import httpx
from datetime import datetime, timedelta

REDIS_URL = os.getenv("REDIS_URL", "rediss://default:AcOQAAIjcDEzOGI2OWU1MzYxZDQ0YWQ2YWU3ODJlNWNmMGY5MjIzY3AxMA@literate-toucan-50064.upstash.io:6379")
VENOM_URL = os.getenv("VENOM_URL", "http://195.26.250.62:3000")
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
                f"{VENOM_URL}/notificar-chat-humano",
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

        # --- BLOQUEO DE NÚMEROS ---
        empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
        empleados_ids = [e.id for e in empleados]
        bloqueado = db.query(BlockedNumber).filter(
            (BlockedNumber.telefono == telefono) &
            (BlockedNumber.empleado_id.in_(empleados_ids)) &
            (BlockedNumber.cliente_id == tenant.id)
        ).first() if empleados_ids else False
        if bloqueado:
            return JSONResponse(content={"mensaje": ""}, status_code=200)

        now = time.time()
        state = get_user_state(telefono)
        
        # Si no hay estado previo o es muy antiguo, crear estado inicial
        if not state or now - state.get("last_interaction", 0) > SESSION_TTL:
            state = {"step": "welcome", "last_interaction": now, "mode": "bot", "is_first_contact": True}
        else:
            state["last_interaction"] = now

        # --- MANEJO DE MODO HUMANO ---
        # Si el usuario está en modo humano, solo responder a comandos específicos
        if state.get("mode") == "human":
            if mensaje in ["bot", "volver", "Bot", "VOLVER", "BOT"]:
                state["mode"] = "bot"
                state["step"] = "welcome"
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": "🤖 El asistente virtual está activo nuevamente. Escribe \"Turno\" para agendar."})
            else:
                # Usuario sigue en modo humano, reenviar mensaje al asesor
                try:
                    import asyncio
                    asyncio.create_task(notificar_chat_humano_completo(tenant.id, telefono, mensaje))
                except Exception as e:
                    print(f"⚠️ Error enviando notificación: {e}")
                # NO actualizar estado aquí para mantener el modo humano
                return JSONResponse(content={"mensaje": ""})  # Respuesta vacía para no confundir

        # --- SOLICITUD DE AYUDA ---
        # Verificar si solicita ayuda ANTES de cualquier otra lógica
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
                
                
                # Asegurar que reserva.fecha_reserva sea timezone-aware
                
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

        if state.get("step") == "welcome":
            if state.get("is_first_contact"):
                state["is_first_contact"] = False
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": generar_mensaje_bienvenida(tenant)})

            if "turno" in mensaje or "reservar" in mensaje or "agendar" in mensaje:
                servicios = tenant.servicios
                empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                # Si hay servicios, mostrar servicios
                if servicios:
                    msg = "¿Qué servicio deseas reservar?\n"
                    for i, s in enumerate(servicios, 1):
                        msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                    msg += "\nResponde con el número del servicio."
                    state["step"] = "waiting_servicio"
                    state["servicios"] = [s.id for s in servicios]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                # Si no hay servicios pero sí empleados, mostrar empleados
                elif empleados:
                    msg = "¿Con qué empleado deseas reservar?\n"
                    for i, e in enumerate(empleados, 1):
                        msg += f"🔹{i}. {e.nombre}\n"
                    msg += "\nResponde con el número del empleado."
                    state["step"] = "waiting_empleado_sin_servicio"
                    state["empleados"] = [e.id for e in empleados]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                # Si NO hay empleados pero hay calendar_id_general, mostrar turnos generales
                elif tenant.calendar_id_general:
                    duracion = 30  # o el valor por defecto que prefieras
                    slots = get_available_slots(
                        calendar_id=tenant.calendar_id_general,
                        credentials_json=GOOGLE_CREDENTIALS_JSON,
                        working_hours_json=tenant.working_hours_general,
                        service_duration=duracion,
                        intervalo_entre_turnos=tenant.intervalo_entre_turnos or 20,  # <-- usa el valor de la base
                        max_turnos=25,
                        cantidad=servicio.cantidad or 1,
                        solo_horas_exactas=servicio.solo_horas_exactas
                    )
                    ahora = datetime.now(pytz.timezone("America/Montevideo"))
                    slots_futuros = [s for s in slots if s > ahora]
                    slots_mostrar = slots_futuros[:25]
                    if not slots_mostrar:
                        return JSONResponse(content={"mensaje": "⚠️ No hay turnos disponibles en este momento."})
                    msg = "📅 Estos son los próximos turnos disponibles:\n"
                    for i, slot in enumerate(slots_mostrar, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    state["step"] = "waiting_turno_final_general"
                    state["slots"] = [s.isoformat() for s in slots_mostrar]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                else:
                    return JSONResponse(content={"mensaje": "⚠️ No hay servicios ni empleados disponibles para reservar turnos en este momento."})

        # Nuevo paso: elegir empleado sin servicio
        if state.get("step") == "waiting_empleado_sin_servicio":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                empleados_ids = state.get("empleados", [])
                if 0 <= idx < len(empleados_ids):
                    empleado_id = empleados_ids[idx]
                    empleado = db.query(Empleado).get(empleado_id)
                    # Buscar el primer servicio disponible para ese empleado, o usar un valor por defecto de duración
                    servicio = db.query(Servicio).filter_by(tenant_id=tenant.id).first()
                    duracion = servicio.duracion if servicio else 30  # 30 min por defecto si no hay servicios
                    slots = get_available_slots(
                        calendar_id=empleado.calendar_id,
                        credentials_json=GOOGLE_CREDENTIALS_JSON,
                        working_hours_json=empleado.working_hours,
                        service_duration=duracion,
                        intervalo_entre_turnos=20,
                        max_turnos=25,
                        cantidad=servicio.cantidad or 1,
                        solo_horas_exactas=servicio.solo_horas_exactas
                    )
                    ahora = datetime.now(pytz.timezone("America/Montevideo"))
                    slots_futuros = [s for s in slots if s > ahora]
                    slots_mostrar = slots_futuros[:25]
                    if not slots_mostrar:
                        return JSONResponse(content={"mensaje": "⚠️ No hay turnos disponibles para este empleado."})
                    msg = "📅 Estos son los próximos turnos disponibles:\n"
                    for i, slot in enumerate(slots_mostrar, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    state["step"] = "waiting_turno_final"
                    state["empleado_id"] = empleado_id
                    state["slots"] = [s.isoformat() for s in slots_mostrar]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                else:
                    empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                    msg = "❌ Opción inválida.\n¿Con qué empleado deseas reservar?\n"
                    for i, e in enumerate(empleados, 1):
                        msg += f"🔹{i}. {e.nombre}\n"
                    msg += "\nResponde con el número del empleado."
                    state["step"] = "waiting_empleado_sin_servicio"
                    state["empleados"] = [e.id for e in empleados]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
            else:
                empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                msg = "❌ Opción inválida.\n¿Con qué empleado deseas reservar?\n"
                for i, e in enumerate(empleados, 1):
                    msg += f"🔹{i}. {e.nombre}\n"
                msg += "\nResponde con el número del empleado."
                state["step"] = "waiting_empleado_sin_servicio"
                state["empleados"] = [e.id for e in empleados]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})

        if state.get("step") == "waiting_servicio":
            print("🔹 Paso: waiting_servicio")
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                servicios_ids = state.get("servicios", [])
                print("🔹 Servicios IDs:", servicios_ids)
                if 0 <= idx < len(servicios_ids):
                    servicio_id = servicios_ids[idx]
                    print("🔹 Servicio seleccionado:", servicio_id)
                    servicio = db.query(Servicio).get(servicio_id)
                    print("🔹 Servicio obtenido:", servicio)
                    duracion = servicio.duracion
                    slots = get_available_slots(
                        calendar_id=tenant.calendar_id_general,
                        credentials_json=GOOGLE_CREDENTIALS_JSON,
                        working_hours_json=tenant.working_hours_general,
                        service_duration=duracion,
                        intervalo_entre_turnos=tenant.intervalo_entre_turnos or 20,  # <-- usa el valor de la base
                        max_turnos=25,
                        cantidad=servicio.cantidad or 1,
                        solo_horas_exactas=servicio.solo_horas_exactas
                    )
                    ahora = datetime.now(pytz.timezone("America/Montevideo"))
                    slots_futuros = [s for s in slots if s > ahora]
                    # Filtrar slots según la cantidad de canchas
                    slots_disponibles = [s for s in slots_futuros if hay_disponibilidad(db, servicio, s)]
                    if not slots_disponibles:
                        return JSONResponse(content={"mensaje": "⚠️ No hay turnos disponibles para este servicio."})
                    msg = "📅 Estos son los próximos turnos disponibles:\n"
                    for i, slot in enumerate(slots_disponibles, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    state["step"] = "waiting_turno_final_canchas"
                    state["servicio_id"] = servicio_id
                    state["slots"] = [s.isoformat() for s in slots_disponibles]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                else:
                    servicios = tenant.servicios
                    msg = "❌ Opción inválida.\n¿Qué servicio deseas reservar?\n"
                    for i, s in enumerate(servicios, 1):
                        msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                    msg += "\nResponde con el número del servicio."
                    state["step"] = "waiting_servicio"
                    state["servicios"] = [s.id for s in servicios]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
            else:
                servicios = tenant.servicios
                msg = "❌ Opción inválida.\n¿Qué servicio deseas reservar?\n"
                for i, s in enumerate(servicios, 1):
                    msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                msg += "\nResponde con el número del servicio."
                state["step"] = "waiting_servicio"
                state["servicios"] = [s.id for s in servicios]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})

        if state.get("step") == "waiting_empleado":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                empleados_ids = state.get("empleados", [])
                if 0 <= idx < len(empleados_ids):
                    empleado_id = empleados_ids[idx]
                    empleado = db.query(Empleado).get(empleado_id)
                    servicio = db.query(Servicio).get(state["servicio_id"])
                    slots = get_available_slots(
                        calendar_id=empleado.calendar_id,
                        credentials_json=GOOGLE_CREDENTIALS_JSON,
                        working_hours_json=empleado.working_hours,
                        service_duration=servicio.duracion,    
                        intervalo_entre_turnos=20,             
                        max_turnos=25,
                        cantidad=servicio.cantidad or 1,
                        solo_horas_exactas=servicio.solo_horas_exactas
                    )
                    ahora = datetime.now(pytz.timezone("America/Montevideo"))
                    slots_futuros = [s for s in slots if s > ahora]
                    max_turnos = 25
                    slots_mostrar = slots_futuros[:max_turnos]
                    if not slots_mostrar:
                        return JSONResponse(content={"mensaje": "⚠️ No hay turnos disponibles para este empleado."})
                    msg = "📅 Estos son los próximos turnos disponibles:\n"
                    for i, slot in enumerate(slots_mostrar, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    state["step"] = "waiting_turno_final"
                    state["empleado_id"] = empleado_id
                    state["slots"] = [s.isoformat() for s in slots_mostrar]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                else:
                    empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                    msg = "❌ Opción inválida.\n¿Con qué empleado?\n"
                    for i, e in enumerate(empleados, 1):
                        msg += f"🔹{i}. {e.nombre}\n"
                    msg += "\nResponde con el número del empleado."
                    state["step"] = "waiting_empleado"
                    state["empleados"] = [e.id for e in empleados]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
            else:
                empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                msg = "❌ Opción inválida.\n¿Con qué empleado?\n"
                for i, e in enumerate(empleados, 1):
                    msg += f"🔹{i}. {e.nombre}\n"
                msg += "\nResponde con el número del empleado."
                state["step"] = "waiting_empleado"
                state["empleados"] = [e.id for e in empleados]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})
        
        if state.get("step") == "waiting_turno_final_canchas":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                if 0 <= idx < len(slots):
                    slot = slots[idx]
                    state["slot"] = slot.isoformat()
                    state["step"] = "waiting_nombre_canchas"
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": "Por favor, escribe tu nombre y apellido para confirmar la reserva."})
                else:
                    # Recalcula todos los slots disponibles
                    servicio = db.query(Servicio).get(state["servicio_id"])
                    slots = get_available_slots(
                        calendar_id=tenant.calendar_id_general,
                        credentials_json=GOOGLE_CREDENTIALS_JSON,
                        working_hours_json=tenant.working_hours_general,
                        service_duration=servicio.duracion,
                        intervalo_entre_turnos=tenant.intervalo_entre_turnos or 20,
                        max_turnos=25,
                        cantidad=servicio.cantidad or 1,
                        solo_horas_exactas=servicio.solo_horas_exactas
                    )
                    ahora = datetime.now(pytz.timezone("America/Montevideo"))
                    slots_futuros = [s for s in slots if s > ahora]
                    slots_disponibles = [s for s in slots_futuros if hay_disponibilidad(db, servicio, s)]
                    msg = "❌ Opción inválida.\n📅 Estos son los próximos turnos disponibles:\n"
                    for i, slot in enumerate(slots_disponibles, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    state["step"] = "waiting_turno_final_canchas"
                    state["slots"] = [s.isoformat() for s in slots_disponibles]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
            else:
                # Mismo recalculo aquí
                servicio = db.query(Servicio).get(state["servicio_id"])
                slots = get_available_slots(
                    calendar_id=tenant.calendar_id_general,
                    credentials_json=GOOGLE_CREDENTIALS_JSON,
                    working_hours_json=tenant.working_hours_general,
                    service_duration=servicio.duracion,
                    intervalo_entre_turnos=tenant.intervalo_entre_turnos or 20,
                    max_turnos=25,
                    cantidad=servicio.cantidad or 1,
                        solo_horas_exactas=servicio.solo_horas_exactas
                )
                ahora = datetime.now(pytz.timezone("America/Montevideo"))
                slots_futuros = [s for s in slots if s > ahora]
                slots_disponibles = [s for s in slots_futuros if hay_disponibilidad(db, servicio, s)]
                msg = "❌ Opción inválida.\n📅 Estos son los próximos turnos disponibles:\n"
                for i, slot in enumerate(slots_disponibles, 1):
                    msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                state["step"] = "waiting_turno_final_canchas"
                state["slots"] = [s.isoformat() for s in slots_disponibles]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})
        
        if state.get("step") == "waiting_nombre_general":
            nombre_apellido = mensaje.strip().title()
            slot = state.get("slot")
            if isinstance(slot, str):
                slot = datetime.fromisoformat(slot)
            # Verifica disponibilidad en calendar general
            from api.utils.calendar_utils import build_service
            service = build_service(GOOGLE_CREDENTIALS_JSON)
            start_time = slot.isoformat()
            end_time = (slot + timedelta(minutes=30)).isoformat()  # Puedes ajustar la duración si lo deseas
            events_result = service.events().list(
                calendarId=tenant.calendar_id_general,
                timeMin=start_time,
                timeMax=end_time,
                singleEvents=True
            ).execute()
            events = events_result.get('items', [])
            if events:
                slots_actuales = get_available_slots(
                    calendar_id=tenant.calendar_id_general,
                    credentials_json=GOOGLE_CREDENTIALS_JSON,
                    working_hours_json=None,
                    service_duration=30,
                    intervalo_entre_turnos=20,
                    max_turnos=10
                )
                ahora = datetime.now(pytz.timezone("America/Montevideo"))
                slots_futuros = [s for s in slots_actuales if s > ahora]
                msg = "❌ El turno seleccionado ya no está disponible. Por favor, elige otro:\n"
                for i, s in enumerate(slots_futuros, 1):
                    msg += f"🔹{i}. {s.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                state["step"] = "waiting_turno_final_general"
                state["slots"] = [s.isoformat() for s in slots_futuros]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})

            # Crear evento en Google Calendar general
            event_id = create_event(
                calendar_id=tenant.calendar_id_general,
                slot_dt=slot,
                user_phone=telefono,
                service_account_info=GOOGLE_CREDENTIALS_JSON,
                duration_minutes=30,  # Puedes ajustar la duración si lo deseas
                client_service=f"Cliente: {nombre_apellido} - Tel: {telefono}"
            )
            fake_id = generar_fake_id()
            reserva = Reserva(
                fake_id=fake_id,
                event_id=event_id,
                empresa=tenant.comercio,
                empleado_id=None,
                empleado_nombre="(Sin asignar)",
                empleado_calendar_id=tenant.calendar_id_general,
                cliente_nombre=nombre_apellido,
                cliente_telefono=telefono,
                fecha_reserva=slot,
                servicio="(General)",
                estado="activo"
            )
            db.add(reserva)
            db.commit()
            state.clear()
            set_user_state(telefono, state)
            return JSONResponse(content={"mensaje": (
                f"✅ {nombre_apellido}, tu turno fue reservado con éxito para el {slot.strftime('%d/%m %H:%M')}.\n"
                f"\nDirección: {tenant.direccion or '📍 a confirmar con el asesor'}\n"
                f"\nSi querés cancelar, escribí: cancelar {fake_id}"
            )})

        if state.get("step") == "waiting_turno_final":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                if 0 <= idx < len(slots):
                    slot = slots[idx]
                    empleado = db.query(Empleado).get(state["empleado_id"])
                    servicio = db.query(Servicio).get(state["servicio_id"])
                    state["slot"] = slot.isoformat()
                    state["empleado_id"] = empleado.id
                    state["servicio_id"] = servicio.id
                    state["step"] = "waiting_nombre"
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": "Por favor, escribe tu nombre y apellido para confirmar la reserva."})
                else:
                    slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                    msg = "❌ Opción inválida.\n📅 Estos son los próximos turnos disponibles:\n"
                    for i, slot in enumerate(slots, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    state["step"] = "waiting_turno_final"
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
            else:
                slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                msg = "❌ Opción inválida.\n📅 Estos son los próximos turnos disponibles:\n"
                for i, slot in enumerate(slots, 1):
                    msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                state["step"] = "waiting_turno_final"
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})

        elif state.get("step") == "waiting_nombre":
            nombre_apellido = mensaje.strip().title()
            slot = state.get("slot")
            if isinstance(slot, str):
                slot = datetime.fromisoformat(slot)
            empleado = db.query(Empleado).get(state["empleado_id"])
            servicio = db.query(Servicio).get(state["servicio_id"])

            # Verifica disponibilidad
            from api.utils.calendar_utils import build_service
            service = build_service(GOOGLE_CREDENTIALS_JSON)
            start_time = slot.isoformat()
            end_time = (slot + timedelta(minutes=servicio.duracion)).isoformat()
            events_result = service.events().list(
                calendarId=empleado.calendar_id,
                timeMin=start_time,
                timeMax=end_time,
                singleEvents=True
            ).execute()
            events = events_result.get('items', [])
            # Si el slot ya no está disponible, mostrar turnos actualizados y volver al paso anterior
            if events or not hay_disponibilidad(db, servicio, slot):
                slots_actuales = get_available_slots(
                    calendar_id=empleado.calendar_id,
                    credentials_json=GOOGLE_CREDENTIALS_JSON,
                    working_hours_json=empleado.working_hours,
                    service_duration=servicio.duracion,
                    intervalo_entre_turnos=20,
                    max_turnos=10,
                    cantidad=servicio.cantidad or 1,
                    solo_horas_exactas=servicio.solo_horas_exactas
                )
                ahora = datetime.now(pytz.timezone("America/Montevideo"))
                slots_futuros = [s for s in slots_actuales if s > ahora]
                slots_disponibles = [s for s in slots_futuros if hay_disponibilidad(db, servicio, s)]
                msg = "❌ El turno seleccionado ya no está disponible. Por favor, elige otro:\n"
                for i, s in enumerate(slots_disponibles, 1):
                    msg += f"🔹{i}. {s.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                state["step"] = "waiting_turno_final"
                state["slots"] = [s.isoformat() for s in slots_disponibles]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})

            # Si está disponible, crear evento y continuar flujo normalmente
            # Crear evento en Google Calendar
            event_id = create_event(
                calendar_id=empleado.calendar_id,
                slot_dt=slot,
                user_phone=telefono,
                service_account_info=GOOGLE_CREDENTIALS_JSON,
                duration_minutes=servicio.duracion,
                client_service=f"Cliente: {nombre_apellido} - Tel: {telefono} - Servicio: {servicio.nombre}"
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
                servicio=servicio.nombre,
                estado="activo"
            )
            db.add(reserva)
            db.commit()
            state.clear()
            set_user_state(telefono, state)
            return JSONResponse(content={"mensaje": (
                f"✅ {nombre_apellido}, tu turno fue reservado con éxito para el {slot.strftime('%d/%m %H:%M')} con {empleado.nombre}.\n"
                f"\nServicio: {servicio.nombre}\n"
                f"Dirección: {tenant.direccion or '📍 a confirmar con el asesor'}\n"
                f"\nSi querés cancelar, escribí: cancelar {fake_id}"
            )})

        # Nuevo paso: esperar nombre para reservas sin empleado
        if state.get("step") == "waiting_nombre_canchas":
            nombre_apellido = mensaje.strip().title()
            slot = state.get("slot")
            if isinstance(slot, str):
                slot = datetime.fromisoformat(slot)
            servicio = db.query(Servicio).get(state["servicio_id"])
            # Verifica disponibilidad antes de crear la reserva
            if not hay_disponibilidad(db, servicio, slot):
                # Mostrar turnos actualizados
                slots = get_available_slots(
                    calendar_id=tenant.calendar_id_general,
                    credentials_json=GOOGLE_CREDENTIALS_JSON,
                    working_hours_json=None,
                    service_duration=servicio.duracion,
                    intervalo_entre_turnos=20,
                    max_turnos=10,
                    cantidad=servicio.cantidad or 1,
                        solo_horas_exactas=servicio.solo_horas_exactas
                )
                ahora = datetime.now(pytz.timezone("America/Montevideo"))
                slots_futuros = [s for s in slots if s > ahora]
                slots_disponibles = [s for s in slots_futuros if hay_disponibilidad(db, servicio, s)]
                msg = "❌ El turno seleccionado ya no está disponible. Por favor, elige otro:\n"
                for i, s in enumerate(slots_disponibles, 1):
                    msg += f"🔹{i}. {s.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                state["step"] = "waiting_turno_final_canchas"
                state["slots"] = [s.isoformat() for s in slots_disponibles]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})

            # Crear evento en Google Calendar
            event_id = create_event(
                calendar_id=tenant.calendar_id_general,
                slot_dt=slot,
                user_phone=telefono,
                service_account_info=GOOGLE_CREDENTIALS_JSON,
                duration_minutes=servicio.duracion,
                client_service=f"Cliente: {nombre_apellido} - Tel: {telefono} - Servicio: {servicio.nombre}"
            )
            fake_id = generar_fake_id()
            reserva = Reserva(
                fake_id=fake_id,
                event_id=event_id,
                empresa=tenant.comercio,
                empleado_id=None,
                empleado_nombre="(Sin asignar)",
                empleado_calendar_id=tenant.calendar_id_general,
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
                f"\nServicio: {servicio.nombre}\n"
                f"Dirección: {tenant.direccion or '📍 a confirmar con el asesor'}\n"
                f"\nSi querés cancelar, escribí: cancelar {fake_id}"
            )})

        # Mensaje genérico por defecto - manejar saludos
        if mensaje in ["hola", "hello", "hi", "buenas", "buen dia", "buenas tardes", "buenas noches"]:
            state["step"] = "welcome"
            state["is_first_contact"] = True  # Tratar saludos como primer contacto
            set_user_state(telefono, state)
            return JSONResponse(content={"mensaje": generar_mensaje_bienvenida(tenant)})
        
        # Manejar palabras clave básicas en cualquier momento de la conversación
        if "turno" in mensaje or "reservar" in mensaje or "agendar" in mensaje:
            servicios = tenant.servicios
            empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
            # Si hay servicios, mostrar servicios
            if servicios:
                msg = "¿Qué servicio deseas reservar?\n"
                for i, s in enumerate(servicios, 1):
                    msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                msg += "\nResponde con el número del servicio."
                state["step"] = "waiting_servicio"
                state["servicios"] = [s.id for s in servicios]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})
            # Si no hay servicios pero sí empleados, mostrar empleados
            elif empleados:
                msg = "¿Con qué empleado deseas reservar?\n"
                for i, e in enumerate(empleados, 1):
                    msg += f"🔹{i}. {e.nombre}\n"
                msg += "\nResponde con el número del empleado."
                state["step"] = "waiting_empleado_sin_servicio"
                state["empleados"] = [e.id for e in empleados]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})
            # Si NO hay empleados pero hay calendar_id_general, mostrar turnos generales
            elif tenant.calendar_id_general:
                duracion = 30  # o el valor por defecto que prefieras
                slots = get_available_slots(
                    calendar_id=tenant.calendar_id_general,
                    credentials_json=GOOGLE_CREDENTIALS_JSON,
                    working_hours_json=tenant.working_hours_general,
                    service_duration=duracion,
                    intervalo_entre_turnos=tenant.intervalo_entre_turnos or 20,  # <-- usa el valor de la base
                    max_turnos=25,
                    cantidad=servicio.cantidad or 1,
                        solo_horas_exactas=servicio.solo_horas_exactas
                )
                ahora = datetime.now(pytz.timezone("America/Montevideo"))
                slots_futuros = [s for s in slots if s > ahora]
                slots_mostrar = slots_futuros[:25]
                if not slots_mostrar:
                    return JSONResponse(content={"mensaje": "⚠️ No hay turnos disponibles en este momento."})
                msg = "📅 Estos son los próximos turnos disponibles:\n"
                for i, slot in enumerate(slots_mostrar, 1):
                    msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                state["step"] = "waiting_turno_final_general"
                state["slots"] = [s.isoformat() for s in slots_mostrar]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})
            else:
                return JSONResponse(content={"mensaje": "⚠️ No hay servicios ni empleados disponibles para reservar turnos en este momento."})
        
        if "informacion" in mensaje or "info" in mensaje:
            if tenant.informacion_local:
                state["step"] = "after_info"
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": f"{tenant.informacion_local}\n\n¿Qué deseas hacer?\n🔹 Escribe \"Turno\" para agendar\n🔹 Escribe \"Ayuda\" para hablar con un asesor"})
            else:
                return JSONResponse(content={"mensaje": "⚠️ No hay información disponible en este momento."})
        
        return JSONResponse(content={"mensaje": "❓ No entendí tu mensaje.\n\n¿Qué necesitas?\n🔹 Escribe \"Turno\" para agendar\n🔹 Escribe \"Información\" para conocer más sobre nosotros\n🔹 Escribe \"Ayuda\" para hablar con un asesor"})

    except Exception as e:
        import traceback as tb
        error_text = tb.format_exc()
        log = ErrorLog(
            cliente=tenant.comercio if 'tenant' in locals() and tenant else None,
            telefono=telefono if 'telefono' in locals() else None,
            mensaje=mensaje if 'mensaje' in locals() else None,
            error=error_text
        )
        db.add(log)
        db.commit()
        print("❌ Error general procesando mensaje:", e)
        # Reiniciar el estado para que el usuario pueda seguir interactuando
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



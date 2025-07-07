from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session
from api.app.models import Tenant, Servicio, Empleado, Reserva, ErrorLog, BlockedNumber
from api.app.deps import get_db
from api.utils.calendar_utils import get_available_slots, create_event, cancelar_evento_google
from api.utils.generador_fake_id import generar_fake_id
import time
import re
import traceback
import os
import pytz
import redis
import json
import httpx
from datetime import datetime, timedelta

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
VENOM_URL = os.getenv("VENOM_URL", "https://backend-agenda-us92.onrender.com")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
SESSION_TTL = 300  # segundos
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

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
        if not state or now - state.get("last_interaction", 0) > SESSION_TTL:
            state = {"step": "welcome", "last_interaction": now, "mode": "bot"}
        else:
            state["last_interaction"] = now
        set_user_state(telefono, state)

        # Lógica de flujo (igual que antes, pero en vez de enviar mensaje, solo devuelve el texto)
        if state.get("mode") == "human":
            if mensaje in ["bot", "volver", "Bot"]:
                state["mode"] = "bot"
                state["step"] = "welcome"
                set_user_state(telefono, state)
                return {"mensaje": "🤖 El asistente virtual está activo nuevamente. Escribe \"Turno\" para agendar."}
            else:
                return {"mensaje": "🚪 Un asesor te responderá a la brevedad. Puedes escribir \"Bot\" y volveré a ayudarte 😊"}

        if any(x in mensaje for x in ["gracias", "chau", "chao", "nos vemos"]):
            return {"mensaje": "😊 ¡Gracias por tu mensaje! Que tengas un buen día!"}

        if "ayuda" in mensaje:
            state["mode"] = "human"
            set_user_state(telefono, state)
            return {"mensaje": "🚪 Un asesor te responderá a la brevedad. Puedes escribir \"Bot\" y volveré a ayudarte 😊"}

        if re.match(r"^cancelar\s+\w+", mensaje):
            partes = mensaje.strip().split(maxsplit=1)
            if len(partes) < 2:
                return {"mensaje": "❌ Debes escribir: cancelar + código"}
            fake_id = partes[1].strip().upper()
            try:
                reserva = db.query(Reserva).filter_by(fake_id=fake_id).first()
                if not reserva:
                    return {"mensaje": "❌ No se encontró la reserva. Verifica el código."}
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
                    return {"mensaje": "✅ Tu turno fue cancelado correctamente."}
                else:
                    return {"mensaje": "❌ No se pudo cancelar el turno. Intenta más tarde."}
            except Exception as e:
                print("❌ Error al cancelar turno:", e)
                return {"mensaje": "❌ No se pudo cancelar el turno. Intenta más tarde."}

        if state.get("step") == "welcome":
            if "turno" in mensaje:
                servicios = tenant.servicios
                if not servicios:
                    return {"mensaje": "⚠️ No hay servicios disponibles."}
                msg = "¿Qué servicio deseas reservar?\n"
                for i, s in enumerate(servicios, 1):
                    msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                msg += "\nResponde con el número del servicio."
                state["step"] = "waiting_servicio"
                state["servicios"] = [s.id for s in servicios]
                set_user_state(telefono, state)
                return {"mensaje": msg}
            else:
                state["step"] = "waiting_turno"
                set_user_state(telefono, state)
                return {"mensaje": f"✋ Hola! Soy el asistente virtual de *{tenant.comercio}*\nEscribe \"Turno\" para agendar\n o \"Ayuda\" para hablar con un asesor."}

        if state.get("step") == "waiting_turno" and "turno" in mensaje:
            servicios = tenant.servicios
            if not servicios:
                return {"mensaje": "⚠️ No hay servicios disponibles."}
            msg = "¿Qué servicio deseas reservar?\n"
            for i, s in enumerate(servicios, 1):
                msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
            msg += "\nResponde con el número del servicio."
            state["step"] = "waiting_servicio"
            state["servicios"] = [s.id for s in servicios]
            set_user_state(telefono, state)
            return {"mensaje": msg}

        if state.get("step") == "waiting_servicio":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                servicios_ids = state.get("servicios", [])
                if 0 <= idx < len(servicios_ids):
                    servicio_id = servicios_ids[idx]
                    servicio = db.query(Servicio).get(servicio_id)
                    empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                    if not empleados:
                        return {"mensaje": "⚠️ No hay empleados disponibles."}
                    msg = f"¿Con qué empleado?\n"
                    for i, e in enumerate(empleados, 1):
                        msg += f"🔹{i}. {e.nombre}\n"
                    msg += "\nResponde con el número del empleado."
                    state["step"] = "waiting_empleado"
                    state["servicio_id"] = servicio_id
                    state["empleados"] = [e.id for e in empleados]
                    set_user_state(telefono, state)
                    return {"mensaje": msg}
                else:
                    servicios = tenant.servicios
                    msg = "❌ Opción inválida.\n¿Qué servicio deseas reservar?\n"
                    for i, s in enumerate(servicios, 1):
                        msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                    msg += "\nResponde con el número del servicio."
                    state["step"] = "waiting_servicio"
                    state["servicios"] = [s.id for s in servicios]
                    set_user_state(telefono, state)
                    return {"mensaje": msg}
            else:
                servicios = tenant.servicios
                msg = "❌ Opción inválida.\n¿Qué servicio deseas reservar?\n"
                for i, s in enumerate(servicios, 1):
                    msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                msg += "\nResponde con el número del servicio."
                state["step"] = "waiting_servicio"
                state["servicios"] = [s.id for s in servicios]
                set_user_state(telefono, state)
                return {"mensaje": msg}

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
                        max_turnos=25
                    )
                    ahora = datetime.now(pytz.timezone("America/Montevideo"))
                    slots_futuros = [s for s in slots if s > ahora]
                    max_turnos = 25
                    slots_mostrar = slots_futuros[:max_turnos]
                    if not slots_mostrar:
                        return {"mensaje": "⚠️ No hay turnos disponibles para este empleado."}
                    msg = "📅 Estos son los próximos turnos disponibles:\n"
                    for i, slot in enumerate(slots_mostrar, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    state["step"] = "waiting_turno_final"
                    state["empleado_id"] = empleado_id
                    state["slots"] = [s.isoformat() for s in slots_mostrar]
                    set_user_state(telefono, state)
                    return {"mensaje": msg}
                else:
                    empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                    msg = "❌ Opción inválida.\n¿Con qué empleado?\n"
                    for i, e in enumerate(empleados, 1):
                        msg += f"🔹{i}. {e.nombre}\n"
                    msg += "\nResponde con el número del empleado."
                    state["step"] = "waiting_empleado"
                    state["empleados"] = [e.id for e in empleados]
                    set_user_state(telefono, state)
                    return {"mensaje": msg}
            else:
                empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                msg = "❌ Opción inválida.\n¿Con qué empleado?\n"
                for i, e in enumerate(empleados, 1):
                    msg += f"🔹{i}. {e.nombre}\n"
                msg += "\nResponde con el número del empleado."
                state["step"] = "waiting_empleado"
                state["empleados"] = [e.id for e in empleados]
                set_user_state(telefono, state)
                return {"mensaje": msg}

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
                    return {"mensaje": "Por favor, escribe tu nombre y apellido para confirmar la reserva."}
                else:
                    slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                    msg = "❌ Opción inválida.\n📅 Estos son los próximos turnos disponibles:\n"
                    for i, slot in enumerate(slots, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    state["step"] = "waiting_turno_final"
                    set_user_state(telefono, state)
                    return {"mensaje": msg}
            else:
                slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                msg = "❌ Opción inválida.\n📅 Estos son los próximos turnos disponibles:\n"
                for i, slot in enumerate(slots, 1):
                    msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                state["step"] = "waiting_turno_final"
                set_user_state(telefono, state)
                return {"mensaje": msg}

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
            if events:
                slots_actuales = get_available_slots(
                    calendar_id=empleado.calendar_id,
                    credentials_json=GOOGLE_CREDENTIALS_JSON,
                    working_hours_json=empleado.working_hours,
                    service_duration=servicio.duracion,
                    intervalo_entre_turnos=20,
                    max_turnos=10
                )
                msg = "❌ El turno seleccionado ya no está disponible. Por favor, elige otro:\n"
                for i, s in enumerate(slots_actuales, 1):
                    msg += f"🔹{i}. {s.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                state["step"] = "waiting_turno_final"
                state["slots"] = [s.isoformat() for s in slots_actuales]
                set_user_state(telefono, state)
                return {"mensaje": msg}

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
            return {"mensaje": (
                f"✅ {nombre_apellido}, tu turno fue reservado con éxito para el {slot.strftime('%d/%m %H:%M')} con {empleado.nombre}.\n"
                f"\nServicio: {servicio.nombre}\n"
                f"Dirección: {tenant.direccion or '📍 a confirmar con el asesor'}\n"
                f"\nSi querés cancelar, escribí: cancelar {fake_id}"
            )}

        # Mensaje genérico por defecto
        return {"mensaje": "❓ No entendí tu mensaje. Escribe \"Turno\" para agendar o \"Ayuda\" para hablar con una persona."}

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
        if not state.get("error_sent"):
            state["error_sent"] = True
            set_user_state(telefono, state)
        return {"mensaje": "❌ Ocurrió un error inesperado. Por favor, intenta nuevamente más tarde."}

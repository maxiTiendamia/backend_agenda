from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session
from api.app.models import Tenant, Servicio, Empleado, Reserva
from api.app.deps import get_db
from api.utils.whatsapp import send_whatsapp_message
from api.utils.calendar_utils import get_available_slots, create_event
import time
import traceback
import os
from api.utils.calendar_utils import cancelar_evento_google
from api.utils.generador_fake_id import generar_fake_id

router = APIRouter()

USER_STATE_CACHE = {}
SESSION_TTL = 300 
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")

@router.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(content=challenge)
    return PlainTextResponse(content="Verification failed", status_code=403)

@router.post("/webhook")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            return JSONResponse(content={"status": "no messages"}, status_code=200)

        from_number = messages[0]["from"]
        message_text = messages[0]["text"]["body"].strip().lower()
        phone_number_id = value.get("metadata", {}).get("phone_number_id")

        tenant = db.query(Tenant).filter_by(phone_number_id=phone_number_id).first()
        if not tenant:
            return JSONResponse(content={"error": "Cliente no encontrado"}, status_code=404)

        now = time.time()
        state = USER_STATE_CACHE.get(from_number)
        if not state or now - state.get("last_interaction", 0) > SESSION_TTL:
            state = {"step": "welcome", "last_interaction": now, "mode": "bot"}
            USER_STATE_CACHE[from_number] = state
        else:
            state["last_interaction"] = now
        
        if state.get("mode") == "human" and now - state.get("last_interaction", 0) > SESSION_TTL:
            state["mode"] = "bot"
            state["step"] = "welcome"
            
        if any(x in message_text for x in ["gracias", "chau", "chao", "nos vemos"]):
            await send_whatsapp_message(
                to=from_number,
                text="😊 ¡Gracias por tu mensaje! Que tengas un buen día!",
                token=ACCESS_TOKEN,
                phone_number_id=tenant.phone_number_id
            )
            return {"status": "respuesta de despedida"}

        if "ayuda" in message_text:
            state["mode"] = "human"
            await send_whatsapp_message(
                to=from_number,
                text="🚪 Un asesor te responderá a la brevedad.",
                token=ACCESS_TOKEN,
                phone_number_id=tenant.phone_number_id
            )
            return {"status": "modo humano activado"}
                # --- BLOQUE DE CANCELACIÓN ---
        if message_text.startswith("cancelar "):
            fake_id = message_text.split(" ", 1)[1].strip().upper()
            try:
                reserva = db.query(Reserva).filter_by(fake_id=fake_id).first()
                if not reserva:
                    await send_whatsapp_message(
                        to=from_number,
                        text="❌ No se encontró la reserva. Verifica el ID.",
                        token=ACCESS_TOKEN,
                        phone_number_id=tenant.phone_number_id
                        )
                    return {"status": "cancelación fallida"}
                exito = cancelar_evento_google(
                    calendar_id=reserva.empleado_calendar_id,
                    reserva_id=reserva.event_id,
                    service_account_info=GOOGLE_CREDENTIALS_JSON
                    )
                if exito:
                    reserva.estado = "cancelado"
                    db.commit()
                    await send_whatsapp_message(
                        to=from_number,
                        text="✅ Tu turno fue cancelado correctamente.",
                        token=ACCESS_TOKEN,
                        phone_number_id=tenant.phone_number_id
                        )
                    state.clear()
                    return {"status": "turno cancelado"}
                else:
                    await send_whatsapp_message(
                        to=from_number,
                        text="❌ No se pudo cancelar el turno. Intenta más tarde.",
                        token=ACCESS_TOKEN,
                        phone_number_id=tenant.phone_number_id
                        )
                    return {"status": "cancelación fallida"}
            except Exception as e:
                print("❌ Error al cancelar turno:", e)
                await send_whatsapp_message(
                    to=from_number,
                    text="❌ Error interno al cancelar el turno.",
                    token=ACCESS_TOKEN,
                    phone_number_id=tenant.phone_number_id
                    )
                return {"status": "error cancelación"}
        # --- FIN BLOQUE DE CANCELACIÓN ---
        
        if state.get("step") == "welcome":
            await send_whatsapp_message(
                to=from_number,
                text=f"✋ Hola! Soy el asistente virtual para *{tenant.comercio}*\nEscribe \"Turno\" para agendar\n o \"Ayuda\" para hablar con un asesor.",
                token=ACCESS_TOKEN,
                phone_number_id=tenant.phone_number_id
            )
            state["step"] = "waiting_turno"
            return {"status": "mensaje bienvenida enviado"}

        if state.get("step") == "waiting_turno" and "turno" in message_text:
            servicios = tenant.servicios
            if not servicios:
                await send_whatsapp_message(
                    to=from_number,
                    text="⚠️ No hay servicios disponibles.",
                    token=ACCESS_TOKEN,
                    phone_number_id=tenant.phone_number_id
                )
                return {"status": "sin servicios"}
            msg = "¿Qué servicio deseas reservar?\n"
            for i, s in enumerate(servicios, 1):
                msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
            msg += "\nResponde con el número del servicio."
            await send_whatsapp_message(
                to=from_number,
                text=msg,
                token=ACCESS_TOKEN,
                phone_number_id=tenant.phone_number_id
            )
            state["step"] = "waiting_servicio"
            state["servicios"] = [s.id for s in servicios]
            return {"status": "servicios enviados"}

        if state.get("step") == "waiting_servicio" and message_text.isdigit():
            idx = int(message_text) - 1
            servicios_ids = state.get("servicios", [])
            if 0 <= idx < len(servicios_ids):
                servicio_id = servicios_ids[idx]
                servicio = db.query(Servicio).get(servicio_id)
                empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                if not empleados:
                    await send_whatsapp_message(
                        to=from_number,
                        text="⚠️ No hay empleados disponibles.",
                        token=ACCESS_TOKEN,
                        phone_number_id=tenant.phone_number_id
                    )
                    return {"status": "sin empleados"}
                msg = f"¿Con qué empleado?\n"
                for i, e in enumerate(empleados, 1):
                    msg += f"🔹{i}. {e.nombre}\n"
                msg += "\nResponde con el número del empleado."
                await send_whatsapp_message(
                    to=from_number,
                    text=msg,
                    token=ACCESS_TOKEN,
                    phone_number_id=tenant.phone_number_id
                )
                state["step"] = "waiting_empleado"
                state["servicio_id"] = servicio_id
                state["empleados"] = [e.id for e in empleados]
                return {"status": "empleados enviados"}
            else:
                # Resetea el estado y vuelve a mostrar los servicios
                servicios = tenant.servicios
                msg = "❌ Opción inválida.\n¿Qué servicio deseas reservar?\n"
                for i, s in enumerate(servicios, 1):
                    msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                msg += "\nResponde con el número del servicio."
                await send_whatsapp_message(
                    to=from_number,
                    text=msg,
                    token=ACCESS_TOKEN,
                    phone_number_id=tenant.phone_number_id
                    )
                state["step"] = "waiting_servicio"
                state["servicios"] = [s.id for s in servicios]
                return {"status": "servicio inválido"}

        if state.get("step") == "waiting_empleado" and message_text.isdigit():
            idx = int(message_text) - 1
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
                    max_turnos=10
                    )
                from datetime import datetime
                import pytz
                ahora = datetime.now(pytz.timezone("America/Montevideo"))
                slots_futuros = [s for s in slots if datetime.strptime(s + f"/{ahora.year}", "%d/%m %H:%M/%Y").replace(tzinfo=ahora.tzinfo) > ahora]
                # Limitar la cantidad máxima de turnos ofrecidos
                max_turnos = 10
                slots_mostrar = slots_futuros[:max_turnos]
                if not slots_mostrar:
                    await send_whatsapp_message(
                        to=from_number,
                        text="⚠️ No hay turnos disponibles para este empleado.",
                        token=ACCESS_TOKEN,
                        phone_number_id=tenant.phone_number_id
                    )
                    return {"status": "sin turnos"}
                msg = "📅 Estos son los próximos turnos disponibles:\n"
                for i, slot in enumerate(slots, 1):
                    msg += f"🔹{i}. {slot}\n"
                msg += "\nResponde con el número del turno."
                await send_whatsapp_message(
                    to=from_number,
                    text=msg,
                    token=ACCESS_TOKEN,
                    phone_number_id=tenant.phone_number_id
                )
                state["step"] = "waiting_turno_final"
                state["empleado_id"] = empleado_id
                state["slots"] = slots_mostrar
                return {"status": "turnos enviados"}
            else:
                # Opción inválida, vuelve a mostrar la lista de empleados
                empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                msg = "❌ Opción inválida.\n¿Con qué empleado?\n"
                for i, e in enumerate(empleados, 1):
                    msg += f"🔹{i}. {e.nombre}\n"
                msg += "\nResponde con el número del empleado."
                await send_whatsapp_message(
                    to=from_number,
                    text=msg,
                    token=ACCESS_TOKEN,
                    phone_number_id=tenant.phone_number_id
                    )
                state["step"] = "waiting_empleado"
                state["empleados"] = [e.id for e in empleados]
                return {"status": "empleado inválido"}
        
        if state.get("step") == "waiting_turno_final" and message_text.isdigit():
            idx = int(message_text) - 1
            slots = state.get("slots", [])
            if 0 <= idx < len(slots):
                slot = slots[idx]
                empleado = db.query(Empleado).get(state["empleado_id"])
                servicio = db.query(Servicio).get(state["servicio_id"])
                # Guardar datos temporales en el estado
                state["slot"] = slot
                state["empleado_id"] = empleado.id
                state["servicio_id"] = servicio.id
                state["step"] = "waiting_nombre"
                await send_whatsapp_message(
                    to=from_number,
                    text="Por favor, escribe tu nombre y apellido para confirmar la reserva.",
                    token=ACCESS_TOKEN,
                    phone_number_id=tenant.phone_number_id
                    )
                return {"status": "pidiendo nombre"}

        elif state.get("step") == "waiting_nombre":
            nombre_apellido = message_text.strip().title()
            slot = state.get("slot")  # El slot ya fue guardado antes
            empleado = db.query(Empleado).get(state["empleado_id"])
            servicio = db.query(Servicio).get(state["servicio_id"])
            event_id = create_event(
                calendar_id=empleado.calendar_id,
                slot_str=slot,
                user_phone=from_number,
                service_account_info=GOOGLE_CREDENTIALS_JSON,
                duration_minutes=servicio.duracion,
                client_service=f"Cliente: {nombre_apellido or ''} - Tel: {from_number} - Servicio: {servicio.nombre}"
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
                cliente_telefono=from_number,
                servicio=servicio.nombre,
                estado="activo"
                )
            db.add(reserva)
            db.commit()
            await send_whatsapp_message(
                to=from_number,
                text=(
                    f"✅ {nombre_apellido}, tu turno fue reservado con éxito para el {slot} con {empleado.nombre}.\n"
                    f"Servicio: {servicio.nombre}\n"
                    f"Dirección: {tenant.direccion or '📍 a confirmar con el asesor'}\n"
                    f"\nSi querés cancelar, escribí: cancelar {fake_id}"
                    ),
                token=ACCESS_TOKEN,
                phone_number_id=tenant.phone_number_id
                )
            state.clear()
            return {"status": "turno reservado", "fake_id": fake_id}
        
        await send_whatsapp_message(
            to=from_number,
            text="❓ No entendí tu mensaje. Escribe \"Turno\" para agendar o \"Ayuda\" para hablar con un asesor.",
            token=ACCESS_TOKEN,
            phone_number_id=tenant.phone_number_id
        )
        return JSONResponse(content={"status": "mensaje no reconocido"})

    except Exception as e:
        print("❌ Error general procesando mensaje:", e)
        traceback.print_exc()
        return JSONResponse(content={"error": "Error interno"}, status_code=500)

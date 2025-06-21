from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse, PlainTextResponse
from app.models import Tenant, Servicio, Empleado, Reserva
from app.deps import get_db
from utils.calendar_utils import get_available_slots, create_event, eliminar_evento
from utils.whatsapp import send_whatsapp_message, obtener_texto, obtener_numero_cliente, obtener_phone_number_id
from utils.config import GOOGLE_CREDENTIALS_JSON, VERIFY_TOKEN
from uuid import uuid4
import time
import traceback
import datetime

USER_STATE_CACHE = {}
SESSION_TTL = 600  # 10 minutos
SLOT_DURATION_MINUTES = 40

router = APIRouter()

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
        # Compatibilidad: si vienen de WhatsApp oficial, usar helpers, si no, fallback a tu lógica original
        try:
            from_number = obtener_numero_cliente(data)
            message_text = obtener_texto(data).lower().strip()
            phone_number_id = obtener_phone_number_id(data)
        except Exception:
            entry = data.get('entry', [{}])[0]
            changes = entry.get('changes', [{}])[0]
            value = changes.get('value', {})
            messages = value.get('messages', [])
            if not messages:
                return JSONResponse(content={"status": "no messages"}, status_code=200)
            from_number = messages[0]['from']
            message_text = messages[0]['text']['body'].strip().lower()
            phone_number_id = value.get("metadata", {}).get("phone_number_id")

        tenant = db.query(Tenant).filter_by(phone_number_id=phone_number_id).first()
        if not tenant:
            return JSONResponse(content={"error": "Cliente no encontrado"}, status_code=404)

        WELCOME_MESSAGE = (
            f"✋ Hola! Soy el asistente virtual para *{tenant.comercio}*\n"
            "Escribe \"Turno\" para agendar\n"
            "o \"Ayuda\" para hablar con un asesor."
        )

        now = time.time()
        state = USER_STATE_CACHE.get(from_number)
        if not state or now - state.get("last_interaction", 0) > SESSION_TTL:
            state = {"step": "welcome", "last_interaction": now, "mode": "bot"}
        else:
            state["last_interaction"] = now
        USER_STATE_CACHE[from_number] = state

        # Respuesta automática a frases comunes
        if any(x in message_text for x in ["gracias", "chau", "chao", "nos vemos"]):
            await send_whatsapp_message(
                from_number,
                "😊 ¡Gracias por tu mensaje! Que tengas un buen día!",
                tenant.access_token,
                phone_number_id
            )
            return {"status": "respuesta de despedida"}

        # Activar modo humano
        if "ayuda" in message_text:
            state["mode"] = "human"
            state["step"] = "human"
            await send_whatsapp_message(
                from_number,
                "🚪 Un asesor te responderá a la brevedad.",
                tenant.access_token,
                phone_number_id
            )
            return {"status": "modo humano activado"}

        if state.get("mode") == "human" or state.get("step") == "human":
            return {"status": "modo humano - sin respuesta"}

        # Cancelar por ID
        if message_text.startswith("cancelar"):
            parts = message_text.split()
            if len(parts) == 2:
                id_reserva = parts[1]
                reserva = db.query(Reserva).filter_by(id=id_reserva, tenant_id=tenant.id).first()
                if reserva:
                    eliminar_evento(tenant.calendar_id, reserva.evento_google_id, GOOGLE_CREDENTIALS_JSON)
                    db.delete(reserva)
                    db.commit()
                    await send_whatsapp_message(
                        from_number,
                        "✅ Reserva cancelada exitosamente.",
                        tenant.access_token,
                        phone_number_id
                    )
                else:
                    await send_whatsapp_message(
                        from_number,
                        "❌ ID de reserva no encontrado.",
                        tenant.access_token,
                        phone_number_id
                    )
                return JSONResponse({"status": "cancel intent processed"})

        # Iniciar nuevo flujo de reserva
        if message_text in ["hola", "turno", "quiero un turno"] or state["step"] == "welcome":
            servicios = db.query(Servicio).filter_by(tenant_id=tenant.id).all()
            if not servicios:
                await send_whatsapp_message(
                    from_number,
                    "⚠️ No hay servicios disponibles.",
                    tenant.access_token,
                    phone_number_id
                )
                return JSONResponse({"status": "no_services"})
            mensaje = "📋 Selecciona un servicio:\n"
            for idx, serv in enumerate(servicios, 1):
                mensaje += f"{idx}. {serv.nombre} (${serv.precio}, {serv.duracion} min)\n"
            state.update({"step": "select_service", "servicios": servicios})
            await send_whatsapp_message(
                from_number,
                mensaje,
                tenant.access_token,
                phone_number_id
            )
            return JSONResponse({"status": "awaiting_service"})

        if state["step"] == "select_service" and message_text.isdigit():
            index = int(message_text) - 1
            servicios = state.get("servicios")
            if 0 <= index < len(servicios):
                servicio = servicios[index]
                empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                if not empleados:
                    await send_whatsapp_message(
                        from_number,
                        "⚠️ No hay empleados disponibles.",
                        tenant.access_token,
                        phone_number_id
                    )
                    return JSONResponse({"status": "no_employees"})
                mensaje = "👤 Selecciona un empleado:\n"
                for idx, emp in enumerate(empleados, 1):
                    mensaje += f"{idx}. {emp.nombre}\n"
                state.update({"step": "select_employee", "servicio": servicio, "empleados": empleados})
                await send_whatsapp_message(
                    from_number,
                    mensaje,
                    tenant.access_token,
                    phone_number_id
                )
                return JSONResponse({"status": "awaiting_employee"})

        if state["step"] == "select_employee" and message_text.isdigit():
            index = int(message_text) - 1
            empleados = state.get("empleados")
            if 0 <= index < len(empleados):
                empleado = empleados[index]
                slots = get_available_slots(
                    calendar_id=empleado.calendar_id,
                    credentials_json=GOOGLE_CREDENTIALS_JSON,
                    working_hours_json=tenant.working_hours,
                    duration_minutes=state["servicio"].duracion
                )
                if not slots:
                    await send_whatsapp_message(
                        from_number,
                        "⚠️ Sin turnos disponibles para este empleado.",
                        tenant.access_token,
                        phone_number_id
                    )
                    return JSONResponse({"status": "no_slots"})
                mensaje = "🗓️ Turnos disponibles:\n"
                for idx, slot in enumerate(slots, 1):
                    mensaje += f"{idx}. {slot}\n"
                state.update({"step": "select_slot", "empleado": empleado, "slots": slots})
                await send_whatsapp_message(
                    from_number,
                    mensaje,
                    tenant.access_token,
                    phone_number_id
                )
                return JSONResponse({"status": "awaiting_slot"})

        if state["step"] == "select_slot" and message_text.isdigit():
            index = int(message_text) - 1
            slots = state.get("slots")
            if 0 <= index < len(slots):
                state.update({"step": "get_name", "selected_slot": slots[index]})
                await send_whatsapp_message(
                    from_number,
                    "✏️ Escribe tu nombre y apellido para confirmar la reserva.",
                    tenant.access_token,
                    phone_number_id
                )
                return JSONResponse({"status": "awaiting_name"})

        if state["step"] == "get_name":
            nombre_cliente = message_text.title()
            servicio = state["servicio"]
            empleado = state["empleado"]
            slot = state["selected_slot"]
            event_id = create_event(
                empleado.calendar_id, slot, from_number, GOOGLE_CREDENTIALS_JSON,
                servicio.duracion, nombre_cliente
            )
            reserva_id = str(uuid4())[:8]
            reserva = Reserva(
                id=reserva_id,
                tenant_id=tenant.id,
                empleado_id=empleado.id,
                servicio_id=servicio.id,
                nombre_cliente=nombre_cliente,
                fecha_hora=datetime.datetime.now(),
                evento_google_id=event_id
            )
            db.add(reserva)
            db.commit()
            await send_whatsapp_message(
                from_number,
                f"✅ Reserva confirmada para {slot}.\nID: {reserva_id}\nSi deseas cancelar, envía: cancelar {reserva_id}",
                tenant.access_token,
                phone_number_id
            )
            state["step"] = "welcome"
            return JSONResponse({"status": "reservation_confirmed"})

        # Si nada coincidió, fallback a tu lógica original de slots rápidos
        if "turno" in message_text and state.get("step") == "welcome":
            slots = get_available_slots(
                calendar_id=tenant.calendar_id,
                credentials_json=GOOGLE_CREDENTIALS_JSON,
                working_hours_json=tenant.working_hours,
                duration_minutes=SLOT_DURATION_MINUTES
            )
            state["slots"] = slots
            if not slots:
                await send_whatsapp_message(
                    from_number,
                    "⚠️ No hay turnos disponibles en este momento. Intenta más tarde.",
                    tenant.access_token,
                    phone_number_id
                )
                return {"status": "sin turnos"}
            response = "📅 Estos son los próximos turnos disponibles:\n"
            for i, slot in enumerate(slots):
                response += f"🔹 *{i+1}*. {slot}\n"
            response += "\nResponde con el *número* del turno que prefieras."
            await send_whatsapp_message(
                from_number,
                response,
                tenant.access_token,
                phone_number_id
            )
            return {"status": "slots enviados"}

        if message_text.isdigit() and state.get("slots"):
            index = int(message_text) - 1
            slots = state.get("slots", [])
            if not slots:
                slots = get_available_slots(
                    calendar_id=tenant.calendar_id,
                    credentials_json=GOOGLE_CREDENTIALS_JSON,
                    working_hours_json=tenant.working_hours,
                    duration_minutes=SLOT_DURATION_MINUTES
                )
                state["slots"] = slots
            if 0 <= index < len(slots):
                try:
                    event_id = create_event(
                        calendar_id=tenant.calendar_id,
                        slot_str=slots[index],
                        user_phone=from_number,
                        service_account_info=GOOGLE_CREDENTIALS_JSON
                    )
                    await send_whatsapp_message(
                        from_number,
                        f"✅ Tu turno fue reservado con éxito para el {slots[index]}.\nDirección: {tenant.direccion or '📍 a confirmar con el asesor'}",
                        tenant.access_token,
                        phone_number_id
                    )
                    return {"status": "turno reservado", "event_id": event_id}
                except Exception:
                    slots = get_available_slots(
                        calendar_id=tenant.calendar_id,
                        credentials_json=GOOGLE_CREDENTIALS_JSON,
                        working_hours_json=tenant.working_hours,
                        duration_minutes=SLOT_DURATION_MINUTES
                    )
                    state["slots"] = slots
                    retry_msg = "⚠️ El turno ya no está disponible. Elige otra opción:\n"
                    for i, slot in enumerate(slots):
                        retry_msg += f"🔹 *{i+1}*. {slot}\n"
                    await send_whatsapp_message(
                        from_number,
                        retry_msg,
                        tenant.access_token,
                        phone_number_id
                    )
                    return JSONResponse(content={"error": "Turno ocupado"}, status_code=409)

        # Mensaje de bienvenida si no hay slots y está en modo bot
        if not state.get("slots") and state.get("mode") == "bot":
            await send_whatsapp_message(
                from_number,
                WELCOME_MESSAGE,
                tenant.access_token,
                phone_number_id
            )
            return {"status": "mensaje bienvenida enviado"}

        # Si nada coincidió
        await send_whatsapp_message(
            from_number,
            "❓ No entendí tu mensaje. Escribe \"Turno\" para agendar o \"Ayuda\" para hablar con un asesor.",
            tenant.access_token,
            phone_number_id
        )
        return JSONResponse({"status": "unrecognized"})

    except Exception as e:
        print("❌ Error procesando webhook:", e)
        traceback.print_exc()
        return JSONResponse(content={"error": "Error interno"}, status_code=500)
from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse, PlainTextResponse
from app.models import Tenant
from app.deps import get_db
from utils.calendar_utils import get_available_slots, create_event
from utils.message_templates import build_message
from utils.whatsapp import send_whatsapp_message
from utils.config import GOOGLE_CREDENTIALS_JSON, VERIFY_TOKEN
import traceback
import time

USER_STATE_CACHE = {}
SESSION_TTL = 300  # segundos
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
        print("Webhook payload:", data)

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
            state = {"slots": [], "last_interaction": now, "mode": "bot"}
            USER_STATE_CACHE[from_number] = state
        else:
            state["last_interaction"] = now

        if state.get("mode") == "human":
            return {"status": "modo humano - sin respuesta"}

        if "ayuda" in message_text:
            state["mode"] = "human"
            await send_whatsapp_message(
                to=from_number,
                text="🚪 Un asesor te responderá a la brevedad.",
                token=tenant.access_token,
                phone_number_id=tenant.phone_number_id
            )
            return {"status": "modo humano activado"}

        if "turno" in message_text:
            slots = get_available_slots(
                calendar_id=tenant.calendar_id,
                credentials_json=GOOGLE_CREDENTIALS_JSON,
                working_hours_json=tenant.working_hours,
                duration_minutes=SLOT_DURATION_MINUTES
            )
            state["slots"] = slots
            if not slots:
                await send_whatsapp_message(
                    to=from_number,
                    text="⚠️ No hay turnos disponibles en este momento. Intenta más tarde.",
                    token=tenant.access_token,
                    phone_number_id=tenant.phone_number_id
                )
                return {"status": "sin turnos"}
            response = "📅 Estos son los próximos turnos disponibles:\n"
            for i, slot in enumerate(slots):
                response += f"🔹 *{i+1}*. {slot}\n"
            response += "\nResponde con el *número* del turno que prefieras."
            await send_whatsapp_message(
                to=from_number,
                text=response,
                token=tenant.access_token,
                phone_number_id=tenant.phone_number_id
            )
            return {"status": "slots enviados"}

        if message_text.isdigit():
            index = int(message_text) - 1
            slots = state.get("slots", [])
            if 0 <= index < len(slots):
                try:
                    event_id = create_event(
                        calendar_id=tenant.calendar_id,
                        slot_str=slots[index],
                        user_phone=from_number,
                        service_account_info=GOOGLE_CREDENTIALS_JSON
                    )
                    await send_whatsapp_message(
                        to=from_number,
                        text=f"✅ Tu turno fue reservado con éxito para el {slots[index]}.\nDirección: {tenant.direccion or '📍 a confirmar con el asesor'}",
                        token=tenant.access_token,
                        phone_number_id=tenant.phone_number_id
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
                        to=from_number,
                        text=retry_msg,
                        token=tenant.access_token,
                        phone_number_id=tenant.phone_number_id
                    )
                    return JSONResponse(content={"error": "Turno ocupado"}, status_code=409)

        if any(x in message_text for x in ["gracias", "chau", "chao", "nos vemos"]):
            await send_whatsapp_message(
                to=from_number,
                text="😊 ¡Gracias por tu mensaje! Que tengas un buen día!",
                token=tenant.access_token,
                phone_number_id=tenant.phone_number_id
            )
            return {"status": "respuesta de despedida"}

        if not state["slots"] and state["mode"] == "bot":
            await send_whatsapp_message(
                to=from_number,
                text=WELCOME_MESSAGE,
                token=tenant.access_token,
                phone_number_id=tenant.phone_number_id
            )
            return {"status": "mensaje bienvenida enviado"}

        await send_whatsapp_message(
            to=from_number,
            text="❓ No entendí tu mensaje. Escribe \"Turno\" para agendar o \"Ayuda\" para hablar con un asesor.",
            token=tenant.access_token,
            phone_number_id=tenant.phone_number_id
        )
        return JSONResponse(content={"status": "mensaje no reconocido"})

    except Exception as e:
        print("❌ Error general procesando mensaje:", e)
        traceback.print_exc()
        return JSONResponse(content={"error": "Error interno"}, status_code=500)
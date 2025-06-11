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

WELCOME_MESSAGE = (
    "✋ Hola! Soy tu asistente virtual.\n"
    "Responde con:\n"
    "1. Para ver los turnos disponibles\n"
    "2. Para solicitar atención personalizada"
)

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
        print("🔍 phone_number_id recibido:", phone_number_id)

        if not phone_number_id:
            return JSONResponse(content={"error": "phone_number_id no encontrado"}, status_code=400)

        tenant = db.query(Tenant).filter_by(phone_number_id=phone_number_id).first()

        if not tenant:
            return JSONResponse(content={"error": "Cliente no encontrado"}, status_code=404)

        if not GOOGLE_CREDENTIALS_JSON:
            return JSONResponse(content={"error": "Credenciales de Google faltantes"}, status_code=500)

        # 1 - Mensaje de bienvenida
        if message_text in ["hola", "hola!", "buenas", "buenos días", "buenas tardes", "buenas noches"]:
            await send_whatsapp_message(
                to=from_number,
                text=WELCOME_MESSAGE,
                token=tenant.access_token,
                phone_number_id=tenant.phone_number_id
            )
            return {"status": "mensaje de bienvenida enviado"}

        # 2 - Ver disponibilidad
        if message_text == "1":
            slots = get_available_slots(tenant.calendar_id, GOOGLE_CREDENTIALS_JSON)
            response = build_message(slots)
            await send_whatsapp_message(
                to=from_number,
                text=response,
                token=tenant.access_token,
                phone_number_id=tenant.phone_number_id
            )
            return {"status": "disponibilidad enviada"}

        # 3 - Atención personalizada
        if message_text == "2":
            await send_whatsapp_message(
                to=from_number,
                text="🚪 Un asesor te responderá a la brevedad.",
                token=tenant.access_token,
                phone_number_id=tenant.phone_number_id
            )
            return {"status": "mensaje de atención enviado"}

        # 4 - Intento de reserva (formato libre tipo 10/06 15:30)
        if "/" in message_text:
            try:
                event_id = create_event(
                    calendar_id=tenant.calendar_id,
                    slot_str=message_text,
                    user_phone=from_number,
                    service_account_info=GOOGLE_CREDENTIALS_JSON
                )
                await send_whatsapp_message(
                    to=from_number,
                    text="✅ Tu turno fue reservado con éxito.",
                    token=tenant.access_token,
                    phone_number_id=tenant.phone_number_id
                )
                return {"status": "turno reservado", "event_id": event_id}
            except Exception as e:
                print("❌ Error creando evento:", e)
                traceback.print_exc()

                # reenviar slots actualizados
                slots = get_available_slots(tenant.calendar_id, GOOGLE_CREDENTIALS_JSON)
                fallback_message = (
                    "⚠️ No pude reservar el turno porque ya está ocupado.\n"
                    + build_message(slots)
                )
                await send_whatsapp_message(
                    to=from_number,
                    text=fallback_message,
                    token=tenant.access_token,
                    phone_number_id=tenant.phone_number_id
                )
                return JSONResponse(content={"error": "Turno ocupado"}, status_code=409)

        # 5 - Mensaje genérico por default
        await send_whatsapp_message(
            to=from_number,
            text="👋 Hola! Puedes escribir '1' para ver turnos o '2' para atención personalizada.",
            token=tenant.access_token,
            phone_number_id=tenant.phone_number_id
        )
        return {"status": "mensaje default enviado"}

    except Exception as e:
        print("❌ Error general procesando mensaje:", e)
        traceback.print_exc()
        return JSONResponse(content={"error": "Error interno"}, status_code=500)
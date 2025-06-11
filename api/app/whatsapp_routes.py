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
import sys

router = APIRouter()

WELCOME_MESSAGE = (
    "👋 Hola! Bienvenido al asistente de turnos.\n"
    "Escribe un número para continuar:\n"
    "1. Ver turnos disponibles\n"
    "2. Atención personalizada"
)

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
        print("Webhook payload:", data, file=sys.stderr)

        entry = data.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        messages = value.get('messages', [])

        if not messages:
            return JSONResponse(content={"status": "no messages"}, status_code=200)

        from_number = messages[0]['from']
        message_text = messages[0]['text']['body'].strip().lower()
        phone_number_id = value.get("metadata", {}).get("phone_number_id")
        print("🔍 phone_number_id recibido:", phone_number_id, file=sys.stderr)

        tenant = db.query(Tenant).filter_by(phone_number_id=phone_number_id).first()
        if not tenant:
            await send_whatsapp_message(to=from_number, text="⚠️ No se encontró el negocio asociado a este número.")
            return JSONResponse(content={"error": "Cliente no encontrado"}, status_code=404)

        if message_text in ["hola", "buenas", "buenos días"]:
            await send_whatsapp_message(to=from_number, text=WELCOME_MESSAGE)
            return {"status": "mensaje bienvenida enviado"}

        elif message_text == "1":
            slots = get_available_slots(tenant.calendar_id, GOOGLE_CREDENTIALS_JSON)
            response = build_message(slots)
            await send_whatsapp_message(to=from_number, text=response)
            return {"status": "slots enviados"}

        elif message_text == "2":
            await send_whatsapp_message(to=from_number, text="📞 En breve un asesor se pondrá en contacto contigo.")
            return {"status": "mensaje personalizado enviado"}

        elif "/" in message_text:
            try:
                event_id = create_event(
                    calendar_id=tenant.calendar_id,
                    slot_str=message_text,
                    user_phone=from_number,
                    service_account_info=GOOGLE_CREDENTIALS_JSON
                )
                await send_whatsapp_message(to=from_number, text="✅ Tu turno fue reservado con éxito.")
                return {"status": "turno reservado", "event_id": event_id}
            except Exception as e:
                print("❌ Error creando evento:", e, file=sys.stderr)
                traceback.print_exc()
                slots = get_available_slots(tenant.calendar_id, GOOGLE_CREDENTIALS_JSON)
                retry_msg = "⚠️ El turno elegido ya no está disponible. Prueba con otra opción:\n" + build_message(slots)
                await send_whatsapp_message(to=from_number, text=retry_msg)
                return JSONResponse(content={"error": "Turno no disponible"}, status_code=409)

        else:
            await send_whatsapp_message(to=from_number, text=WELCOME_MESSAGE)
            return {"status": "mensaje por defecto enviado"}

    except Exception as e:
        print("❌ Error general procesando mensaje:", e, file=sys.stderr)
        traceback.print_exc()
        return JSONResponse(content={"error": "Error interno"}, status_code=500)
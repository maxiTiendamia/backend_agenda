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
        print("📩 Webhook payload:", data)

        entry = data.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        messages = value.get('messages', [])

        if not messages:
            print("⚠️ No messages found in payload")
            return JSONResponse(content={"status": "no messages"}, status_code=200)

        from_number = messages[0]['from']
        message_text = messages[0]['text']['body'].strip().lower()

        phone_number_id = value.get("metadata", {}).get("phone_number_id")
        print("🔍 phone_number_id recibido:", phone_number_id)

        if not phone_number_id:
            return JSONResponse(content={"error": "phone_number_id no encontrado"}, status_code=400)

        tenant = db.query(Tenant).filter_by(phone_number_id=phone_number_id).first()

        if not tenant:
            print("❌ Tenant no encontrado para phone_number_id:", phone_number_id)
            return JSONResponse(content={"error": "Cliente no encontrado"}, status_code=404)

        if not GOOGLE_CREDENTIALS_JSON:
            print("❌ GOOGLE_CREDENTIALS_JSON no configurado")
            return JSONResponse(content={"error": "Credenciales de Google faltantes"}, status_code=500)

        # 🟢 Responder con disponibilidad
        if message_text in ["hola", "turno", "turnos", "quiero un turno"]:
            slots = get_available_slots(tenant.calendar_id, GOOGLE_CREDENTIALS_JSON)
            print("📅 Slots disponibles:", slots)

            response = build_message(slots)
            await send_whatsapp_message(to=from_number, text=response)
            print("✅ Mensaje enviado a", from_number)
            return {"status": "mensaje enviado"}

        # 🟢 Crear evento si mandan fecha
        elif "/" in message_text:
            try:
                event_id = create_event(
                    calendar_id=tenant.calendar_id,
                    slot_str=message_text,
                    user_phone=from_number,
                    service_account_info=GOOGLE_CREDENTIALS_JSON
                )
                print("📆 Evento creado con ID:", event_id)

                await send_whatsapp_message(to=from_number, text="✅ Tu turno fue reservado con éxito.")
                return {"status": "turno reservado", "event_id": event_id}
            except Exception as e:
                print("❌ Error creando evento:", e)
                traceback.print_exc()
                await send_whatsapp_message(to=from_number, text="⚠️ No pude reservar el turno")
                return JSONResponse(content={"error": "Error reservando turno"}, status_code=500)

        # 🟡 Mensaje desconocido
        else:
            await send_whatsapp_message(
                to=from_number,
                text="👋 Hola! Puedes escribirme 'turno' para ver disponibilidad o enviar una fecha como '10/06 15:30' para reservar."
            )
            print("🗨️ Mensaje de ayuda enviado a", from_number)
            return {"status": "respuesta enviada"}

    except Exception as e:
        print("❌ Error general procesando mensaje:", e)
        traceback.print_exc()
        return JSONResponse(content={"error": "Error interno"}, status_code=500)
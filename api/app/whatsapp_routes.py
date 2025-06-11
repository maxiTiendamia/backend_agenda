from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from app.models import Tenant
from app.deps import get_db
from utils.calendar_utils import get_available_slots, create_event
from utils.message_templates import build_message
from utils.whatsapp import send_whatsapp_message
from fastapi.responses import JSONResponse, PlainTextResponse
from utils.config import VERIFY_TOKEN
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
        from_number = data['entry'][0]['changes'][0]['value']['messages'][0]['from']
        message_text = data['entry'][0]['changes'][0]['value']['messages'][0]['text']['body'].strip().lower()

        tenant: Tenant = db.query(Tenant).filter_by(telefono=from_number).first()

        if not tenant:
            return JSONResponse(content={"error": "Cliente no encontrado"}, status_code=404)

        if message_text in ["hola", "turno", "turnos", "quiero un turno"]:
            slots = get_available_slots(tenant.calendar_id, tenant.access_token)
            response = build_message(slots)
            await send_whatsapp_message(
                to=from_number,
                text=response
            )
            return {"status": "mensaje enviado"}

        elif "/" in message_text:
            try:
                event_id = create_event(
                    calendar_id=tenant.calendar_id,
                    slot_str=message_text,
                    user_phone=from_number,
                    service_account_info=tenant.access_token
                )
                await send_whatsapp_message(
                    to=from_number,
                    text="✅ Tu turno fue reservado con éxito."
                )
                return {"status": "turno reservado", "event_id": event_id}
            except Exception as e:
                print("❌ Error creando evento:", e)
                traceback.print_exc()
                await send_whatsapp_message(
                    to=from_number,
                    text="⚠️ No pude reservar el turno"
                )
                return JSONResponse(content={"error": "Error reservando turno"}, status_code=500)

        else:
            await send_whatsapp_message(
                to=from_number,
                text="👋 Hola! Puedes escribirme 'turno' para ver disponibilidad o enviar una fecha como '10/06 15:30' para reservar."
            )
            return {"status": "respuesta enviada"}

    except Exception as e:
        print("❌ Error procesando mensaje:", e)
        traceback.print_exc()
        return JSONResponse(content={"error": "Error interno"}, status_code=500)
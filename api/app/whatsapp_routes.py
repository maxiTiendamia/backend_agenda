from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.database import db
from app.models import Tenant
from utils.calendar_utils import get_available_slots, create_event
from utils.message_templates import build_message
from utils.whatsapp import send_whatsapp_message
import traceback

router = APIRouter()

@router.post("/webhook")
async def whatsapp_webhook(request: Request):
    try:
        data = await request.json()

        # Extraer número de teléfono del mensaje entrante
        from_number = data['entry'][0]['changes'][0]['value']['messages'][0]['from']
        message_text = data['entry'][0]['changes'][0]['value']['messages'][0]['text']['body'].strip().lower()

        with db.session() as session:
            tenant: Tenant = session.query(Tenant).filter_by(telefono=from_number).first()

            if not tenant:
                return JSONResponse(content={"error": "Cliente no encontrado"}, status_code=404)

            # Comandos posibles
            if message_text in ["hola", "turno", "turnos", "quiero un turno"]:
                slots = get_available_slots(tenant.calendar_id, tenant.access_token)
                response = build_message(slots)
                send_whatsapp_message(
                    phone_number_id=tenant.phone_number_id,
                    to=from_number,
                    message=response,
                    token=tenant.access_token
                )
                return {"status": "mensaje enviado"}

            elif "/" in message_text:  # ejemplo de turno "10/06 15:30"
                try:
                    event_id = create_event(
                        calendar_id=tenant.calendar_id,
                        slot_str=message_text,
                        user_phone=from_number,
                        service_account_info=tenant.access_token,
                        summary="Turno reservado",
                        description="Reservado automáticamente por WhatsApp Bot"
                    )
                    send_whatsapp_message(
                        phone_number_id=tenant.phone_number_id,
                        to=from_number,
                        message="✅ Tu turno fue reservado con éxito.",
                        token=tenant.access_token
                    )
                    return {"status": "turno reservado", "event_id": event_id}
                except Exception as e:
                    print("❌ Error creando evento:", e)
                    traceback.print_exc()
                    send_whatsapp_message(
                        phone_number_id=tenant.phone_number_id,
                        to=from_number,
                        message="⚠️ No pude reservar el turno",
                        token=tenant.access_token
                    )
                    return JSONResponse(content={"error": "Error reservando turno"}, status_code=500)

            else:
                send_whatsapp_message(
                    phone_number_id=tenant.phone_number_id,
                    to=from_number,
                    message="👋 Hola! Puedes escribirme 'turno' para ver disponibilidad o enviar una fecha como '10/06 15:30' para reservar.",
                    token=tenant.access_token
                )
                return {"status": "respuesta enviada"}

    except Exception as e:
        print("❌ Error procesando mensaje:", e)
        traceback.print_exc()
        return JSONResponse(content={"error": "Error interno"}, status_code=500)
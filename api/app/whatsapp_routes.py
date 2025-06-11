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

# Diccionario temporal en memoria (podrías reemplazar esto por cache redis o BD si es necesario)
USER_SLOT_CACHE = {}

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
        print("\ud83d\udce9 Webhook payload:", data)

        entry = data.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        messages = value.get('messages', [])

        if not messages:
            print("\u26a0\ufe0f No messages found in payload")
            return JSONResponse(content={"status": "no messages"}, status_code=200)

        from_number = messages[0]['from']
        message_text = messages[0]['text']['body'].strip().lower()
        phone_number_id = value.get("metadata", {}).get("phone_number_id")
        print("\ud83d\udd0d phone_number_id recibido:", phone_number_id)

        if not phone_number_id:
            return JSONResponse(content={"error": "phone_number_id no encontrado"}, status_code=400)

        tenant = db.query(Tenant).filter_by(phone_number_id=phone_number_id).first()

        if not tenant:
            print("\u274c Tenant no encontrado para phone_number_id:", phone_number_id)
            return JSONResponse(content={"error": "Cliente no encontrado"}, status_code=404)

        if not GOOGLE_CREDENTIALS_JSON:
            print("\u274c GOOGLE_CREDENTIALS_JSON no configurado")
            return JSONResponse(content={"error": "Credenciales de Google faltantes"}, status_code=500)

        # Mensaje de bienvenida con opciones
        if message_text in ["hola", "buenas", "hey"]:
            await send_whatsapp_message(to=from_number, text="\ud83d\udc4b Hola! Elige una opci\u00f3n:\n1. Ver turnos disponibles\n2. Atenci\u00f3n personalizada")
            print("\ud83d\udcac Opciones enviadas a", from_number)
            return {"status": "opciones enviadas"}

        # Opción 1 - Mostrar turnos
        if message_text == "1":
            slots = get_available_slots(tenant.calendar_id, GOOGLE_CREDENTIALS_JSON)
            USER_SLOT_CACHE[from_number] = slots  # guardamos para futura reserva
            response = build_message(slots)
            await send_whatsapp_message(to=from_number, text=response)
            print("\ud83d\udcc5 Turnos enviados a", from_number)
            return {"status": "turnos enviados"}

        # Opción 2 - Atención personalizada
        if message_text == "2":
            await send_whatsapp_message(to=from_number, text="\ud83d\udeac En breve un asesor se comunicar\u00e1 contigo.")
            return {"status": "mensaje enviado"}

        # Si responde con un n\u00famero de turno
        if message_text.isdigit():
            index = int(message_text) - 1
            slots = USER_SLOT_CACHE.get(from_number, [])
            if 0 <= index < len(slots):
                try:
                    event_id = create_event(
                        calendar_id=tenant.calendar_id,
                        slot_str=slots[index],
                        user_phone=from_number,
                        service_account_info=GOOGLE_CREDENTIALS_JSON
                    )
                    await send_whatsapp_message(to=from_number, text="\u2705 Tu turno fue reservado con \u00e9xito para el " + slots[index])
                    return {"status": "turno reservado", "event_id": event_id}
                except Exception as e:
                    print("\u274c Error creando evento:", e)
                    traceback.print_exc()
                    await send_whatsapp_message(to=from_number, text="\u26a0\ufe0f No pude reservar el turno. Puede que est\u00e9 ocupado o el formato no sea v\u00e1lido.")
                    return JSONResponse(content={"error": "Error reservando turno"}, status_code=500)
            else:
                await send_whatsapp_message(to=from_number, text="\u26a0\ufe0f Opcion de turno inv\u00e1lida. Intenta de nuevo.")
                return {"status": "turno inv\u00e1lido"}

        # Mensaje gen\u00e9rico de ayuda
        await send_whatsapp_message(
            to=from_number,
            text="\ud83d\udc4b Hola! Elige una opci\u00f3n:\n1. Ver turnos disponibles\n2. Atenci\u00f3n personalizada"
        )
        print("\ud83d\udcac Reenv\u00edo de opciones a", from_number)
        return {"status": "respuesta enviada"}

    except Exception as e:
        print("\u274c Error general procesando mensaje:", e)
        traceback.print_exc()
        return JSONResponse(content={"error": "Error interno"}, status_code=500)
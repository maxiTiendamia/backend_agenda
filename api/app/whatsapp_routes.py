from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.models import Tenant
from app.whatsapp import send_whatsapp_message
from app.calendar_utils import get_available_slots, create_event
from datetime import datetime

router = APIRouter()

# Estado conversacional simple
user_greeted = set()
user_selection = {}

@router.post("/webhook")
async def whatsapp_webhook(request: Request):
    data = await request.json()

    try:
        changes = data.get('entry', [])[0].get('changes', [])[0].get('value', {})
        messages = changes.get('messages')

        if not messages:
            return {"status": "ignored"}

        entry = messages[0]
        user_msg = entry['text']['body']
        from_number = entry['from']

        # Buscar tenant por número de teléfono
        tenant = Tenant.query.filter_by(telefono=from_number).first()
        if not tenant:
            return {"status": "cliente no encontrado"}

        calendar_id = tenant.calendar_id
        service_account_info = tenant.google_service_account_info

        if not calendar_id or not service_account_info:
            return {"status": "datos incompletos"}

        # Primera vez que se contacta el cliente
        if from_number not in user_greeted:
            bienvenida = (
                "Hola 👋 Bienvenido/a a nuestra agenda automatizada.\n"
                "Respondé con el número correspondiente:\n"
                "1️⃣ para reservar un turno\n"
                "2️⃣ para que te contactemos personalmente."
            )
            await send_whatsapp_message(from_number, bienvenida)
            user_greeted.add(from_number)
            return {"status": "greeted"}

        # Si ya seleccionó un turno anteriormente
        if from_number in user_selection and user_msg.isdigit():
            index = int(user_msg) - 1
            slots = user_selection[from_number]
            if 0 <= index < len(slots):
                selected_slot = slots[index]
                create_event(calendar_id, selected_slot, from_number, service_account_info)
                await send_whatsapp_message(from_number, f"✅ Turno reservado para: {selected_slot}")
                del user_selection[from_number]
            else:
                await send_whatsapp_message(from_number, "Número inválido. Elegí una opción válida.")
            return {"status": "handled"}

        # Pedido de turnos
        if user_msg == "1" or "turno" in user_msg.lower():
            slots = get_available_slots(calendar_id, service_account_info)

            unique_slots = []
            seen = set()
            for slot in slots:
                key = datetime.strptime(slot, "%d/%m %H:%M")
                if key not in seen:
                    seen.add(key)
                    unique_slots.append(slot)

            user_selection[from_number] = unique_slots

            if unique_slots:
                msg = "Estos son los próximos turnos disponibles:\n"
                for idx, slot in enumerate(unique_slots):
                    msg += f"{idx+1}. {slot}\n"
                msg += "\nRespondé con el número del turno que querés reservar."
            else:
                msg = "No hay turnos disponibles por el momento."

            await send_whatsapp_message(from_number, msg)

        elif user_msg == "2" or "contacto" in user_msg.lower():
            await send_whatsapp_message(from_number, "Perfecto, en breve nos pondremos en contacto contigo personalmente. 🙌")

        else:
            await send_whatsapp_message(from_number, "¿Querés reservar un turno? Respondé con '1'. Si preferís que te contactemos, respondé con '2'.")

    except Exception as e:
        print(f"❌ Error procesando mensaje: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

    return {"status": "ok"}
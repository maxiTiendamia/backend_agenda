from fastapi import FastAPI, Request, Query
from app.config import VERIFY_TOKEN, CALENDAR_ID
from app.whatsapp import send_whatsapp_message
from app.calendar import get_available_slots, create_event

app = FastAPI()

# Guardar selección temporal (esto puede ir en base de datos en producción)
user_selection = {}
user_greeted = set()

@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/webhook")
def verify_token(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge")
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return int(hub_challenge)
    return {"error": "Invalid token"}, 403

@app.post("/webhook")
async def receive_message(request: Request):
    data = await request.json()
    try:
        changes = data.get('entry', [])[0].get('changes', [])[0].get('value', {})
        messages = changes.get('messages')

        if not messages:
            return {"status": "ignored"}  # No hay mensaje para procesar

        entry = messages[0]
        user_msg = entry['text']['body']
        from_number = entry['from']

        if from_number not in user_greeted:
            bienvenida = (
                "Hola 👋 Bienvenido/a a nuestra agenda automatizada.\n"
                "Podés escribir 'turno' para reservar uno o 'contacto' para que te respondamos personalmente."
            )
            await send_whatsapp_message(from_number, bienvenida)
            user_greeted.add(from_number)
            return {"status": "greeted"}

        if from_number in user_selection and user_msg.isdigit():
            index = int(user_msg) - 1
            slots = user_selection[from_number]
            if 0 <= index < len(slots):
                selected_slot = slots[index]
                create_event(CALENDAR_ID, selected_slot, from_number)
                await send_whatsapp_message(from_number, f"✅ Turno reservado para: {selected_slot}")
                del user_selection[from_number]
            else:
                await send_whatsapp_message(from_number, "Número inválido. Por favor, elige una opción válida.")
            return {"status": "handled"}

        if "turno" in user_msg.lower():
            slots = get_available_slots(CALENDAR_ID)
            user_selection[from_number] = slots
            if slots:
                msg = "Estos son los próximos turnos disponibles:\n"
                for idx, slot in enumerate(slots):
                    msg += f"{idx+1}. {slot}\n"
                msg += "\nRespondé con el número del turno que querés reservar."
            else:
                msg = "No hay turnos disponibles por el momento."
            await send_whatsapp_message(from_number, msg)
        elif "contacto" in user_msg.lower():
            await send_whatsapp_message(from_number, "Perfecto, en breve nos pondremos en contacto contigo personalmente. 🙌")
        else:
            await send_whatsapp_message(from_number, "¿Querés reservar un turno? Escribí 'turno'. Si preferís que te contactemos, escribí 'contacto'.")

    except Exception as e:
        print("Error al procesar:", e)
    return {"status": "received"}
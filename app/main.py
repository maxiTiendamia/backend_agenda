from fastapi import FastAPI, Request, Query
from app.config import VERIFY_TOKEN, CALENDAR_ID
from app.whatsapp import send_whatsapp_message
from app.calendar import get_available_slots, create_event
from datetime import datetime, timedelta

app = FastAPI()

# Guardar selección temporal (esto puede ir en base de datos en producción)
user_selection = {}

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
        entry = data['entry'][0]['changes'][0]['value']['messages'][0]
        user_msg = entry['text']['body']
        from_number = entry['from']

        if from_number in user_selection and user_msg.isdigit():
            index = int(user_msg) - 1
            slots = user_selection[from_number]
            if 0 <= index < len(slots):
                selected_start = slots[index]
                start_dt = datetime.fromisoformat(selected_start)
                end_dt = start_dt + timedelta(minutes=30)
                event_link = create_event(CALENDAR_ID, start_dt.isoformat(), end_dt.isoformat())
                await send_whatsapp_message(from_number, f"✅ Turno reservado para: {selected_start}\nℹ️ Evento: {event_link}")
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
                    formatted = datetime.fromisoformat(slot).strftime("%d/%m %H:%M")
                    msg += f"{idx+1}. {formatted}\n"
                msg += "\nRespondé con el número del turno que querés reservar."
            else:
                msg = "No hay turnos disponibles por el momento."
            await send_whatsapp_message(from_number, msg)
        else:
            await send_whatsapp_message(from_number, "Hola 👋 ¿Querés reservar un turno? Escribí 'turno'.")

    except Exception as e:
        print("Error al procesar:", e)
    return {"status": "received"}

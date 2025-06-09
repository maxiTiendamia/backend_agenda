from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from config import WELCOME_MESSAGE
from calendar_utils import obtener_horarios_disponibles, reservar_turno

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def whatsapp_webhook():
    incoming_msg = request.values.get("Body", "").strip().lower()
    user_number = request.values.get("From", "")

    resp = MessagingResponse()
    msg = resp.message()

    if "hola" in incoming_msg or "turno" in incoming_msg:
        msg.body(WELCOME_MESSAGE)
    elif "ver" in incoming_msg or "disponible" in incoming_msg:
        turnos = obtener_horarios_disponibles()
        if not turnos:
            msg.body("No hay horarios disponibles por ahora 😕")
        else:
            respuesta = "Estos son los próximos horarios disponibles:\n"
            for i, t in enumerate(turnos):
                respuesta += f"{i+1}. {t['hora']} ({t['fecha']})\n"
            respuesta += "\nRespondé con el número del turno que querés reservar."
            msg.body(respuesta)
    elif incoming_msg.isdigit():
        index = int(incoming_msg) - 1
        turno = reservar_turno(index)
        if turno:
            msg.body(f"✅ ¡Listo! Tu turno fue reservado para el {turno['fecha']} a las {turno['hora']}.")
        else:
            msg.body("Ese turno ya no está disponible o hubo un error. Probá con otro 🙏")
    else:
        msg.body("¿Querés reservar un turno? Escribí 'ver horarios' o 'quiero un turno'.")

    return str(resp)

if __name__ == "__main__":
    app.run(debug=True)


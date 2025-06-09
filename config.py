import os

BUSINESS_NAME = "Estética Clara"
WELCOME_MESSAGE = f"Hola 👋, gracias por contactarte con {BUSINESS_NAME}. ¿Querés reservar un turno? Te muestro los horarios disponibles 😊"

TURN_DURATION_MINUTES = 30  # duración del turno en minutos
WORKING_HOURS = {
    "start": "09:00",
    "end": "18:00"
}

CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")  # ID del calendario desde .env
TIMEZONE = "America/Montevideo"
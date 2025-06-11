def build_message(slots: list[str]) -> str:
    if not slots:
        return "😕 Lo siento, no hay turnos disponibles en este momento."

    message = "📅 Estos son los próximos turnos disponibles:\n"
    for i, slot in enumerate(slots, 1):
        message += f"{i}. {slot}\n"
    message += "\nResponde con el número o copia y pega el turno que prefieras."
    return message
import requests
import os

VENOM_BASE_URL = os.getenv("VENOM_URL", "https://backend-agenda-us92.onrender.com")  # URL de tu servicio Venom


def enviar_mensaje_whatsapp(cliente_id: str, numero: str, mensaje: str) -> bool:
    """
    Envía un mensaje a un número específico usando la sesión activa de Venom para el cliente.
    """
    try:
        # Paso 1: asegurar que la sesión esté activa
        iniciar_url = f"{VENOM_BASE_URL}/iniciar/{cliente_id}"
        requests.get(iniciar_url, timeout=10)

        # Paso 2: enviar el mensaje
        payload = {
            "clienteId": cliente_id,
            "to": numero,
            "message": mensaje
        }
        respuesta = requests.post(f"{VENOM_BASE_URL}/send", json=payload, timeout=10)

        if respuesta.status_code == 200:
            print(f"✅ Mensaje enviado a {numero} desde cliente {cliente_id}")
            return True
        else:
            print(f"❌ Error al enviar mensaje: {respuesta.text}")
            return False

    except Exception as e:
        print(f"❌ Error general en envío de mensaje: {e}")
        return False



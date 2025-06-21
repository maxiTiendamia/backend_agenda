import httpx

async def send_whatsapp_message(to: str, text: str, token: str, phone_number_id: str):
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }

    print("🛰️ Enviando mensaje a WhatsApp:", data)

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data)
        print("📬 Respuesta de WhatsApp:", response.status_code, response.text)
        return response.json()



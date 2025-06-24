import secrets
import string

def generar_fake_id(longitud=6):
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(longitud))
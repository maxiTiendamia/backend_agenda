import requests
from admin_app.database import SessionLocal
from admin_app.models import Tenant

def generar_qr_para_cliente(cliente_id):
    url = f"http://localhost:3000/qr/{cliente_id}"  # O tu URL local en dev
    session = SessionLocal()
    try:
        cliente = session.query(Tenant).filter_by(telefono=cliente_id).first()
        if cliente:
            cliente.qr_code = url
            session.commit()
    except Exception as e:
        print(f"❌ Error guardando QR en DB: {e}")
        session.rollback()
    finally:
        session.close()
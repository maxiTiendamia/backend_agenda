import requests
from admin_app.database import SessionLocal
from admin_app.models import Tenant

def generar_qr_para_cliente(cliente_id):
    session = SessionLocal()
    try:
        cliente = session.query(Tenant).filter_by(telefono=cliente_id).first()
        if cliente:
            cliente.qr_code = "https://venom-service.onrender.com/qr/" + cliente_id
            session.commit()
    except Exception as e:
        print("❌ Error:", e)
        session.rollback()
    finally:
        session.close()
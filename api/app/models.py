from datetime import datetime
from database import db
from sqlalchemy.dialects.postgresql import JSON

class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    apellido = db.Column(db.String(50), nullable=True)
    comercio = db.Column(db.String(100), nullable=True)
    telefono = db.Column(db.String(20), nullable=True)
    fecha_creada = db.Column(db.DateTime, default=datetime.utcnow)
    direccion = db.Column(db.String(200), nullable=True)

    calendar_id = db.Column(db.String(100), nullable=True)
    phone_number_id = db.Column(db.String(100), nullable=True)
    verify_token = db.Column(db.String(100), nullable=True)
    access_token = db.Column(db.String(200), nullable=True)
    working_hours = db.Column(JSON)

    def __repr__(self):
        return f"<Tenant {self.nombre}>"
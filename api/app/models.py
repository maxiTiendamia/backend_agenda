from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    apellido = db.Column(db.String(50), nullable=True)
    comercio = db.Column(db.String(100), nullable=True)
    telefono = db.Column(db.String(20), nullable=True)
    fecha_creada = db.Column(db.DateTime, default=datetime.utcnow)

    # Campos que antes estaban en TenantConfig
    business_hours = db.Column(db.Text, nullable=True)
    calendar_id = db.Column(db.String(100), nullable=True)
    phone_number_id = db.Column(db.String(100), nullable=True)
    verify_token = db.Column(db.String(100), nullable=True)
    access_token = db.Column(db.String(200), nullable=True)

    def __repr__(self):
        return f"<Tenant {self.nombre}>"
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    apellido = db.Column(db.String(100), nullable=False)
    comercio = db.Column(db.String(200), nullable=False)
    telefono = db.Column(db.String(20), nullable=False)
    fecha_creada = db.Column(db.DateTime, default=datetime.utcnow)

    # Relación 1 a 1 con configuración
    config = db.relationship("TenantConfig", back_populates="tenant", uselist=False)

    def __str__(self):
        return f"{self.comercio} ({self.nombre} {self.apellido})"


class TenantConfig(db.Model):
    __tablename__ = "tenant_config"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"))
    calendar_id = db.Column(db.String(100), nullable=True)
    phone_number_id = db.Column(db.String(100), nullable=True)
    verify_token = db.Column(db.String(200), nullable=True)
    access_token = db.Column(db.String(200), nullable=True)
    business_hours = db.Column(db.Text, nullable=True)

    # Relación inversa
    tenant = db.relationship("Tenant", back_populates="config")
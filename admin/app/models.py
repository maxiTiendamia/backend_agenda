from app.database import db
from datetime import datetime

class Tenant(db.Model):
    __tablename__ = 'tenants'

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100))
    apellido = db.Column(db.String(100))
    comercio = db.Column(db.String(150))
    telefono = db.Column(db.String(20))
    fecha_creada = db.Column(db.DateTime, default=datetime.utcnow)

    # Relación uno a uno con TenantConfig
    config = db.relationship(
        'TenantConfig',
        back_populates='tenant',
        uselist=False,
        cascade="all, delete-orphan"
    )

class TenantConfig(db.Model):
    __tablename__ = 'tenant_configs'

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False, unique=True)

    calendar_id = db.Column(db.String(200))
    phone_number_id = db.Column(db.String(100))
    verify_token = db.Column(db.String(255))
    access_token = db.Column(db.String(255))
    business_hours = db.Column(db.Text)

    # Relación inversa
    tenant = db.relationship('Tenant', back_populates='config')
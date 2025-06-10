from .database import db
from datetime import datetime

class Tenant(db.Model):
    __tablename__ = 'tenant'

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120))
    apellido = db.Column(db.String(120))
    comercio = db.Column(db.String(120))
    telefono = db.Column(db.String(120))
    fecha_creada = db.Column(db.DateTime, default=datetime.utcnow)

    config = db.relationship('TenantConfig', back_populates='tenant', uselist=False)


class TenantConfig(db.Model):
    __tablename__ = 'tenant_config'

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'))
    calendar_id = db.Column(db.String(120))
    phone_number_id = db.Column(db.String(120))
    verify_token = db.Column(db.String(120))
    access_token = db.Column(db.String(120))
    business_hours = db.Column(db.Text)

    tenant = db.relationship('Tenant', back_populates='config')
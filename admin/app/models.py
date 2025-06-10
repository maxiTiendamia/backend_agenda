from datetime import datetime
from app.database import db

class Tenant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    config = db.relationship('TenantConfig', backref='tenant', uselist=False)
    credentials = db.relationship('TenantCredentials', backref='tenant', uselist=False)
    nombre = db.Column(db.String(128), nullable=False)
    apellido = db.Column(db.String(128), nullable=False)
    telefono = db.Column(db.String(32), nullable=False)
    correo = db.Column(db.String(128), nullable=False)
    comercio = db.Column(db.String(128))  # Opcional
    tipo_comercio = db.Column(db.String(128))
    direccion = db.Column(db.String(256))
    fecha_creada = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_update = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TenantConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'))
    business_hours = db.Column(db.Text)
    calendar_id = db.Column(db.String(256))
    phone_number_id = db.Column(db.String(64))
    verify_token = db.Column(db.String(128))
    access_token = db.Column(db.String(512))  

class TenantCredentials(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'))
    google_service_account_info = db.Column(db.Text)
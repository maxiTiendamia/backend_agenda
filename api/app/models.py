from .database import db
from datetime import datetime

class Tenant(db.Model):
    __tablename__ = 'tenants'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100))
    apellido = db.Column(db.String(100))
    comercio = db.Column(db.String(150))
    telefono = db.Column(db.String(50), unique=True)
    fecha_creada = db.Column(db.DateTime, default=datetime.utcnow)

    config = db.relationship("TenantConfig", backref="tenant", uselist=False, cascade="all, delete-orphan")

class TenantConfig(db.Model):
    __tablename__ = 'tenant_config'
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False, unique=True)
    business_hours = db.Column(db.Text)  # JSON string con horarios por día
    calendar_id = db.Column(db.String(255))
    phone_number_id = db.Column(db.String(100))
    verify_token = db.Column(db.String(255))
    access_token = db.Column(db.Text)
from datetime import datetime
from admin_app.database import db
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
    phone_number_id = db.Column(db.String(400), nullable=True)
    verify_token = db.Column(db.String(400), nullable=True)
    access_token = db.Column(db.String(400), nullable=True)

    servicios = db.relationship('Servicio', back_populates='tenant', cascade="all, delete-orphan")
    empleados = db.relationship('Empleado', back_populates='tenant', cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Tenant {self.nombre}>"


class Servicio(db.Model):
    __tablename__ = "servicios"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    precio = db.Column(db.Float, nullable=False)
    duracion = db.Column(db.Integer, nullable=False)  # minutos

    tenant = db.relationship('Tenant', back_populates='servicios')

    def __repr__(self):
        return f"<Servicio {self.nombre}>"


class Empleado(db.Model):
    __tablename__ = "empleados"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    calendar_id = db.Column(db.String(200), nullable=True)
    working_hours = db.Column(JSON)

    tenant = db.relationship('Tenant', back_populates='empleados')

    def __repr__(self):
        return f"<Empleado {self.nombre}>"
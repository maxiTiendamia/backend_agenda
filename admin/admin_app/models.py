from datetime import datetime, timezone
from admin_app.database import db
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship

class ErrorLog(db.Model):
    __tablename__ = "error_logs"
    id = db.Column(Integer, primary_key=True)
    cliente = db.Column(String(255))
    telefono = db.Column(String(50))
    mensaje = db.Column(Text)
    error = db.Column(Text)
    fecha = db.Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    apellido = db.Column(db.String(50), nullable=True)
    comercio = db.Column(db.String(100), nullable=True)
    telefono = db.Column(db.String(20), nullable=True)
    fecha_creada = db.Column(db.DateTime, default=datetime.utcnow)
    direccion = db.Column(db.String(200), nullable=True)
    qr_code = db.Column(db.Text)
    informacion_local = db.Column(db.Text, nullable=True)  # Nueva columna para información del local
    calendar_id_general = db.Column(db.String, nullable=True)  # <-- Nuevo campo
    servicios = db.relationship('Servicio', back_populates='tenant', cascade="all, delete-orphan")
    empleados = db.relationship('Empleado', back_populates='tenant', cascade="all, delete-orphan")
    working_hours_general = db.Column(Text, nullable=True)  # <-- NUEVO
    intervalo_entre_turnos = db.Column(Integer, default=20) 

    def __repr__(self):
        return f"<Tenant {self.nombre}>"
    
class Reserva(db.Model):
    __tablename__ = "reservas"
    id = db.Column(Integer, primary_key=True)  
    fake_id = db.Column(String(12), unique=True, nullable=False) 
    event_id = db.Column(String(200), nullable=False)  
    empresa = db.Column(String(100), nullable=False) 
    empleado_id = Column(Integer, ForeignKey('empleados.id'), nullable=True)
    empleado_nombre = db.Column(String(100), nullable=False)
    empleado_calendar_id = db.Column(String(200), nullable=False)
    cliente_nombre = db.Column(String(100), nullable=False) 
    cliente_telefono = db.Column(String(20), nullable=False)
    fecha_reserva = db.Column(DateTime, default=datetime.utcnow)
    servicio = db.Column(String(150), nullable=False)
    estado = db.Column(String(20), nullable=False, default="activo") 
    cantidad = db.Column(Integer, default=1)  # <-- NUEVO CAMPO

    empleado = relationship('Empleado')

    def __repr__(self):
        return f"<Reserva {self.fake_id} - {self.empresa} - {self.empleado_nombre}>"

class Servicio(db.Model):
    __tablename__ = "servicios"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    precio = db.Column(db.Float, nullable=False)
    duracion = db.Column(db.Integer, nullable=False)  # minutos
    cantidad = Column(Integer, default=1)
    tenant = db.relationship('Tenant', back_populates='servicios')
    solo_horas_exactas = db.Column(db.Boolean, default=False)  # nuevo campo


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


class BlockedNumber(db.Model):
    __tablename__ = "blocked_numbers"
    
    id = db.Column(db.Integer, primary_key=True)
    empleado_id = db.Column(db.Integer, db.ForeignKey('empleados.id'), nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    telefono = db.Column(db.String(30), nullable=False)
    fecha_bloqueo = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    empleado = db.relationship('Empleado')
    cliente = db.relationship('Tenant')

    def __repr__(self):
        return f"<BlockedNumber {self.telefono} - {self.empleado.nombre if self.empleado else 'N/A'}>"
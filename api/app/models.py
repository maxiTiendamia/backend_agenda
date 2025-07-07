from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, Text
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.dialects.postgresql import JSON
from datetime import datetime, timezone

Base = declarative_base()

class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    nombre = Column(String(50), nullable=False)
    apellido = Column(String(50), nullable=True)
    comercio = Column(String(100), nullable=True)
    telefono = Column(String(20), nullable=True)
    fecha_creada = Column(DateTime, default=datetime.now(timezone.utc))
    direccion = Column(String(200), nullable=True)
    qr_code = Column(Text, nullable=True)
    informacion_local = Column(Text, nullable=True)  # Nueva columna para información del local

    servicios = relationship('Servicio', back_populates='tenant', cascade="all, delete-orphan")
    empleados = relationship('Empleado', back_populates='tenant', cascade="all, delete-orphan")
    
class Reserva(Base):
    __tablename__ = "reservas"
    id = Column(Integer, primary_key=True)  
    fake_id = Column(String(12), unique=True, nullable=False)  
    event_id = Column(String(200), nullable=False) 
    empresa = Column(String(100), nullable=False)  
    empleado_id = Column(Integer, ForeignKey('empleados.id'), nullable=False)
    empleado_nombre = Column(String(100), nullable=False)
    empleado_calendar_id = Column(String(200), nullable=False)
    cliente_nombre = Column(String(100), nullable=False)  
    cliente_telefono = Column(String(20), nullable=False)
    fecha_reserva = Column(DateTime, default=datetime.now(timezone.utc))
    servicio = Column(String(150), nullable=False)
    estado = Column(String(20), nullable=False, default="activo") 

    empleado = relationship('Empleado')

    def __repr__(self):
        return f"<Reserva {self.fake_id} - {self.empresa} - {self.empleado_nombre}>"
class Servicio(Base):
    __tablename__ = "servicios"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    nombre = Column(String(150), nullable=False)
    precio = Column(Float, nullable=False)
    duracion = Column(Integer, nullable=False) 
    tenant = relationship('Tenant', back_populates='servicios')

class Empleado(Base):
    __tablename__ = "empleados"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    nombre = Column(String(100), nullable=False)
    calendar_id = Column(String(200), nullable=True)
    working_hours = Column(JSON)

    tenant = relationship('Tenant', back_populates='empleados')
    
class ErrorLog(Base):
    __tablename__ = "error_logs"
    id = Column(Integer, primary_key=True)
    cliente = Column(String(255))
    telefono = Column(String(50))
    mensaje = Column(Text)
    error = Column(Text)
    fecha = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class BlockedNumber(Base):
    __tablename__ = "blocked_numbers"
    id = Column(Integer, primary_key=True)
    empleado_id = Column(Integer, ForeignKey('empleados.id'), nullable=False)
    cliente_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    telefono = Column(String(30), nullable=False)
    fecha_bloqueo = Column(DateTime, default=datetime.now(timezone.utc))

    empleado = relationship('Empleado')
    cliente = relationship('Tenant')
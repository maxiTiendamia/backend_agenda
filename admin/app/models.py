from sqlalchemy import Column, Integer, String, Float, ForeignKey, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import relationship

class Tenant(db.Model):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    nombre = Column(String(100))
    apellido = Column(String(100))
    comercio = Column(String(150))
    telefono = Column(String(50))
    fecha_creada = Column(TIMESTAMP)
    access_token = Column(String)
    verify_token = Column(String)
    phone_number_id = Column(String)
    calendar_id = Column(String)
    direccion = Column(String(200))
    working_hours = Column(JSON)

class Servicio(db.Model):
    __tablename__ = "servicios"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'))
    nombre = Column(String(150))
    precio = Column(Float)
    duracion = Column(Integer)

class Empleado(db.Model):
    __tablename__ = "empleados"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'))
    nombre = Column(String(100))
    calendar_id = Column(String(200))

class Reserva(db.Model):
    __tablename__ = "reservas"
    id = Column(String(10), primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'))
    empleado_id = Column(Integer, ForeignKey('empleados.id'))
    servicio_id = Column(Integer, ForeignKey('servicios.id'))
    nombre_cliente = Column(String(150))
    fecha_hora = Column(TIMESTAMP)
    evento_google_id = Column(String(150))
    fecha_creada = Column(TIMESTAMP)
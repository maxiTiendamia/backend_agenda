from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.dialects.postgresql import JSON
from datetime import datetime

Base = declarative_base()

class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    nombre = Column(String(50), nullable=False)
    apellido = Column(String(50), nullable=True)
    comercio = Column(String(100), nullable=True)
    telefono = Column(String(20), nullable=True)
    fecha_creada = Column(DateTime, default=datetime.utcnow)
    direccion = Column(String(200), nullable=True)
    calendar_id = Column(String(400), nullable=True)
    phone_number_id = Column(String(400), nullable=True)
    verify_token = Column(String(400), nullable=True)
    access_token = Column(String(400), nullable=True)
    working_hours = Column(JSON)

    servicios = relationship('Servicio', back_populates='tenant', cascade="all, delete-orphan")
    empleados = relationship('Empleado', back_populates='tenant', cascade="all, delete-orphan")

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
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.dialects.postgresql import JSON
from .database import Base

class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(50), nullable=False)
    apellido = Column(String(50), nullable=True)
    comercio = Column(String(100), nullable=True)
    telefono = Column(String(20), nullable=True)
    fecha_creada = Column(DateTime, default=datetime.utcnow)
    direccion = Column(String(200), nullable=True)

    calendar_id = Column(String(100), nullable=True)
    phone_number_id = Column(String(100), nullable=True)
    verify_token = Column(String(100), nullable=True)
    access_token = Column(String(200), nullable=True)
    working_hours = Column(JSON)
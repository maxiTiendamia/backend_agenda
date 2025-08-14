from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, Text, Boolean
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
    qr_code = Column(Text)
    informacion_local = Column(Text, nullable=True)
    calendar_id_general = Column(String, nullable=True)
    working_hours_general = Column(Text, nullable=True)
    intervalo_entre_turnos = Column(Integer, default=20)
    mensaje_bienvenida_personalizado = Column(Text, nullable=True)

    # 🆕 NUEVOS CAMPOS para reservas directas
    calendar_id_directo = Column(String(255), nullable=True)
    duracion_turno_directo = Column(Integer, nullable=True)  # en minutos
    precio_turno_directo = Column(Float, nullable=True)  # puede ser NULL
    solo_horas_exactas_directo = Column(Boolean, default=False)
    turnos_consecutivos_directo = Column(Boolean, default=False)

    # Relaciones con otros modelos
    servicios = relationship('Servicio', back_populates='tenant', cascade="all, delete-orphan", lazy='dynamic')
    empleados = relationship('Empleado', back_populates='tenant', cascade="all, delete-orphan", lazy='dynamic')

    def __repr__(self):
        return f"<Tenant {self.comercio or self.nombre}>"

class Reserva(Base):
    __tablename__ = "reservas"
    id = Column(Integer, primary_key=True)  
    fake_id = Column(String(12), unique=True, nullable=False)  
    event_id = Column(String(200), nullable=False) 
    empresa = Column(String(100), nullable=False)  
    empleado_id = Column(Integer, ForeignKey('empleados.id'), nullable=True)
    empleado_nombre = Column(String(100), nullable=False)
    empleado_calendar_id = Column(String(200), nullable=False)
    cliente_nombre = Column(String(100), nullable=False)  
    cliente_telefono = Column(String(20), nullable=False)
    # 🔧 CORREGIR: Asegurar que siempre use UTC
    fecha_reserva = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    servicio = Column(String(150), nullable=False)
    estado = Column(String(20), nullable=False, default="activo") 
    cantidad = Column(Integer, default=1)

    empleado = relationship('Empleado')

    def __repr__(self):
        return f"<Reserva {self.fake_id} - {self.empresa} - {self.empleado_nombre}>"

class Servicio(Base):
    __tablename__ = "servicios"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    precio = Column(Float, nullable=False)
    duracion = Column(Integer, nullable=False)
    cantidad = Column(Integer, default=1)
    solo_horas_exactas = Column(Boolean, default=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    
    # Campos para calendario y horarios por servicio
    calendar_id = Column(String, nullable=True)
    working_hours = Column(Text, nullable=True)
    
    # Campos para servicios informativos
    es_informativo = Column(Boolean, default=False, nullable=False)
    mensaje_personalizado = Column(Text, nullable=True)
    
    # 🆕 NUEVO CAMPO: Turnos consecutivos sin solapamiento
    turnos_consecutivos = Column(Boolean, default=False, nullable=False)

    # Relación con Tenant
    tenant = relationship("Tenant", back_populates="servicios")

    def __repr__(self):
        return f"<Servicio {self.nombre}>"

class Empleado(Base):
    __tablename__ = "empleados"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    nombre = Column(String(100), nullable=False)
    calendar_id = Column(String(200), nullable=True)
    working_hours = Column(JSON)

    tenant = relationship('Tenant', back_populates='empleados')

    def __repr__(self):
        return f"<Empleado {self.nombre}>"
    
class ErrorLog(Base):
    __tablename__ = "error_logs"
    id = Column(Integer, primary_key=True)
    cliente = Column(String(255))
    telefono = Column(String(50))
    mensaje = Column(Text)
    error = Column(Text)
    # 🔧 CORREGIR: Usar lambda para consistencia
    fecha = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class BlockedNumber(Base):
    __tablename__ = "blocked_numbers"
    id = Column(Integer, primary_key=True)
    empleado_id = Column(Integer, ForeignKey('empleados.id'), nullable=True)
    cliente_id = Column(Integer, ForeignKey('tenants.id'), nullable=False)
    telefono = Column(String(30), nullable=False)
    # 🔧 CORREGIR: Usar lambda para consistencia
    fecha_bloqueo = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    empleado = relationship('Empleado')
    cliente = relationship('Tenant')

    def __repr__(self):
        return f"<BlockedNumber {self.telefono}>"
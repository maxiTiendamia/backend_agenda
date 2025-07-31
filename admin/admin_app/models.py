from datetime import datetime, timezone
from admin_app.database import db
from sqlalchemy.dialects.postgresql import JSON

class ErrorLog(db.Model):
    __tablename__ = "error_logs"
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(255))
    telefono = db.Column(db.String(50))
    mensaje = db.Column(db.Text)
    error = db.Column(db.Text)
    fecha = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<ErrorLog {self.telefono}>"
    
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
    informacion_local = db.Column(db.Text, nullable=True)
    calendar_id_general = db.Column(db.String, nullable=True)
    working_hours_general = db.Column(db.Text, nullable=True)
    intervalo_entre_turnos = db.Column(db.Integer, default=20) 

    # 🔥 CRÍTICO: Definir relaciones explícitamente
    servicios = db.relationship('Servicio', back_populates='tenant', cascade="all, delete-orphan", lazy='dynamic')
    empleados = db.relationship('Empleado', back_populates='tenant', cascade="all, delete-orphan", lazy='dynamic')

    def __repr__(self):
        return f"<Tenant {self.nombre}>"
    
class Reserva(db.Model):
    __tablename__ = "reservas"
    id = db.Column(db.Integer, primary_key=True)  
    fake_id = db.Column(db.String(12), unique=True, nullable=False) 
    event_id = db.Column(db.String(200), nullable=False)  
    empresa = db.Column(db.String(100), nullable=False) 
    empleado_id = db.Column(db.Integer, db.ForeignKey('empleados.id'), nullable=True)
    empleado_nombre = db.Column(db.String(100), nullable=False)
    empleado_calendar_id = db.Column(db.String(200), nullable=False)
    cliente_nombre = db.Column(db.String(100), nullable=False) 
    cliente_telefono = db.Column(db.String(20), nullable=False)
    fecha_reserva = db.Column(db.DateTime, default=datetime.utcnow)
    servicio = db.Column(db.String(150), nullable=False)
    estado = db.Column(db.String(20), nullable=False, default="activo") 
    cantidad = db.Column(db.Integer, default=1)

    empleado = db.relationship('Empleado')

    def __repr__(self):
        return f"<Reserva {self.fake_id} - {self.empresa} - {self.empleado_nombre}>"

class Servicio(db.Model):
    __tablename__ = "servicios"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    precio = db.Column(db.Float, nullable=False)
    duracion = db.Column(db.Integer, nullable=False)  # minutos
    cantidad = db.Column(db.Integer, default=1)
    solo_horas_exactas = db.Column(db.Boolean, default=False)
    
    # 🔥 CAMPOS FALTANTES AGREGADOS:
    calendar_id = db.Column(db.String, nullable=True)
    working_hours = db.Column(db.Text, nullable=True)

    # 🔥 CRÍTICO: Relación inversa explícita
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

    # 🔥 CRÍTICO: Relación inversa explícita
    tenant = db.relationship('Tenant', back_populates='empleados')

    def __repr__(self):
        return f"<Empleado {self.nombre}>"

class BlockedNumber(db.Model):
    __tablename__ = "blocked_numbers"
    
    id = db.Column(db.Integer, primary_key=True)
    empleado_id = db.Column(db.Integer, db.ForeignKey('empleados.id'), nullable=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    telefono = db.Column(db.String(30), nullable=False)
    fecha_bloqueo = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    empleado = db.relationship('Empleado')
    cliente = db.relationship('Tenant')

    def __repr__(self):
        return f"<BlockedNumber {self.telefono}>"
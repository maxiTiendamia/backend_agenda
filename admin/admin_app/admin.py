from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_basicauth import BasicAuth
from flask import render_template, flash, Markup, redirect, request, url_for
from wtforms import Field
from admin_app.models import Tenant, Empleado, Servicio, Reserva, ErrorLog, BlockedNumber
from admin_app.database import db
import json
from sqlalchemy.exc import IntegrityError
from collections import Counter
import os
import requests
import threading

WEBCONNECT_URL = os.getenv("webconnect_url", "http://195.26.250.62:3000")
basic_auth = BasicAuth()

# ⬇️ Nueva función para generar QR en segundo plano
def llamar_a_webconnect_async(cliente_id):
    try:
        webconnect_url = f"{WEBCONNECT_URL}/iniciar/{cliente_id}"
        print(f"🛠️ [Async] Enviando solicitud a webconnect para generar QR del cliente {cliente_id}")
        response = requests.post(webconnect_url, timeout=10)
        if response.ok:
            print("✅ [Async] webconnect generó QR correctamente")
        else:
            print(f"⚠️ [Async] webconnect no respondió correctamente: {response.status_code}")
    except Exception as e:
        print(f"❌ [Async] Error al contactar a webconnect: {e}")


# ⬇️ Nueva función para regenerar QR manualmente
def regenerar_qr_webconnect_async(cliente_id):
    try:
        webconnect_url = f"{WEBCONNECT_URL}/generar-qr/{cliente_id}"
        print(f"🔄 [Async] Regenerando QR para el cliente {cliente_id}")
        response = requests.post(webconnect_url, timeout=10)
        if response.ok:
            print("✅ [Async] QR regenerado correctamente")
        else:
            print(f"⚠️ [Async] Error al regenerar QR: {response.status_code}")
    except Exception as e:
        print(f"❌ [Async] Error al regenerar QR: {e}")


def obtener_estado_sesion(cliente_id):
    try:
        res = requests.get(f"{WEBCONNECT_URL}/estado-sesiones", timeout=10)
        sesiones = res.json()

        for sesion in sesiones:
            if str(sesion.get("clienteId", sesion.get("id", ""))) == str(cliente_id):
                estado = sesion.get("estado", "NO_INICIADA")
                estilos = {
                    "CONNECTED": ("🟢", "#d4edda", "#155724"),
                    "DISCONNECTED": ("🔴", "#f8d7da", "#721c24"),
                    "TIMEOUT": ("🟠", "#fff3cd", "#856404"),
                    "ERROR": ("❌", "#f8d7da", "#721c24"),
                    "ARCHIVOS_DISPONIBLES": ("💾", "#e7f3ff", "#004085"),
                    "NO_INICIADA": ("⚪", "#f8f9fa", "#6c757d"),
                    "UNPAIRED": ("🔴", "#f8d7da", "#721c24"),
                    "UNLAUNCHED": ("🔴", "#f8d7da", "#721c24")
                }
                icono, fondo, color = estilos.get(estado, ("⚪", "#eeeeee", "#333333"))
                info_extra = ""
                if sesion.get("enMemoria"): info_extra += " (En memoria)"
                if sesion.get("tieneArchivos"): info_extra += " (Con archivos)"
                return Markup(
                    f'<div style="background-color:{fondo}; color:{color}; padding:6px 10px; border-radius:5px; display:inline-block;">{icono} {estado}{info_extra}</div><br>'
                    f'<a href="/admin/reiniciar/{cliente_id}" class="btn btn-sm btn-warning" style="margin-top: 4px;" onclick="return confirm(\'¿Seguro que deseas reiniciar esta sesión?\');">Reiniciar</a>'
                )
        return Markup('<span style="background:#e0e0e0; padding:4px 8px; border-radius:5px;">⚪ No iniciada</span>')
    except Exception as e:
        print(f"❌ Error obteniendo estado de sesión para {cliente_id}: {e}")
        return Markup('<span style="background:#ccc; padding:4px 8px; border-radius:5px;">⚠️ Error</span>')


class SecureModelView(ModelView):
    def is_accessible(self):
        return basic_auth.authenticate()

    def inaccessible_callback(self, name, **kwargs):
        return basic_auth.challenge()


class ErrorLogModelView(SecureModelView):    
    can_create = False
    can_edit = False
    can_delete = True
    can_view_details = True
    column_searchable_list = ['cliente', 'telefono', 'mensaje', 'error']
    column_filters = ['cliente', 'telefono', 'fecha']
    column_list = ('id', 'cliente', 'telefono', 'mensaje', 'error', 'fecha')
    form_columns = ('cliente', 'telefono', 'mensaje', 'error', 'fecha')
    column_default_sort = ('fecha', True)


class WorkingHoursWidget:
    def __call__(self, field, **kwargs):
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        existing = json.loads(field.data or '{}')
        html = "<div style='padding: 1rem  0;'>"
        for day in days:
            checked = 'checked' if day in existing else ''
            start = end = ''
            if day in existing and existing[day]:
                interval = existing[day][0].split('-')
                if len(interval) == 2:
                    start, end = interval
            html += f"<div style='margin-bottom: 0.5rem;'><label><input type='checkbox' name='{field.name}_{day}_active' {checked}> {day.title()}</label>"
            html += f" De: <input type='time' name='{field.name}_{day}_start' value='{start}'>"
            html += f" a <input type='time' name='{field.name}_{day}_end' value='{end}'></div>"
        html += "</div>"
        return html


class WorkingHoursField(Field):
    widget = WorkingHoursWidget()

    def process(self, formdata, data=None, extra_filters=None):
        super().process(formdata, data, extra_filters=extra_filters)
        self.formdata = formdata
        self.data = data or "{}"

    def populate_obj(self, obj, name):
        result = {}
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
            active = self.formdata.get(f'{self.name}_{day}_active')
            start = self.formdata.get(f'{self.name}_{day}_start')
            end = self.formdata.get(f'{self.name}_{day}_end')
            if active and start and end:
                result[day] = [f"{start}-{end}"]
        setattr(obj, name, json.dumps(result))

    def process_data(self, value):
        self.data = value


class InformacionLocalWidget:
    def __call__(self, field, **kwargs):
        value = field.data or ""
        html = f"""
        <div style='margin: 1rem 0;'>
            <label for='{field.id}' style='font-weight: bold; margin-bottom: 0.5rem; display: block;'>
                Información del Local
            </label>
            <small style='color: #666; display: block; margin-bottom: 0.5rem;'>
                Este texto se mostrará cuando el cliente solicite información. Puedes incluir:
                ubicación, horarios, servicios, términos y condiciones, etc.
            </small>
            <textarea 
                id='{field.id}' 
                name='{field.name}' 
                class='form-control' 
                rows='10'
                style='width: 100%; resize: vertical;'
                placeholder='Ejemplo:
📍 UBICACIÓN: Av. Principal 123, Centro
⏰ HORARIOS: Lun-Vie 9:00-18:00, Sab 9:00-14:00
🎯 SERVICIOS: Corte, Peinado, Coloración
📋 TÉRMINOS: Cancelaciones hasta 2hs antes'
            >{value}</textarea>
        </div>
        """
        return Markup(html)


class InformacionLocalField(Field):
    widget = InformacionLocalWidget()


class TenantModelView(SecureModelView):
    # 🔥 REMOVER inline_models que está causando problemas
    # inline_models = [...]

    column_list = ('id', 'nombre', 'comercio', 'telefono', 'direccion', 'fecha_creada', 'qr_code', 'estado_wa')
    
    # 🔍 VERIFICAR: Si working_hours_general existe, agregarlo aquí
    form_columns = (
        'nombre', 'apellido', 'comercio', 'telefono', 'direccion',
        'informacion_local', 'working_hours_general', 'intervalo_entre_turnos'
    )

    # 🔥 AGREGAR form_overrides completo
    form_overrides = {
        'informacion_local': InformacionLocalField,
        'working_hours_general': WorkingHoursField,  # 🆕 AGREGAR si existe el campo
    }

    column_formatters = {
        'qr_code': lambda v, c, m, p: Markup(
            f"<img src='data:image/png;base64,{m.qr_code}' style='height:150px;'>"
            ) if m.qr_code and not m.qr_code.startswith("http") and not m.qr_code.startswith("data:image") else (
                Markup(f"<img src='{m.qr_code}' style='height:150px;'>")
                ) if m.qr_code else Markup("<span style='color: gray;'>⏳ Esperando QR...</span>"),
        'estado_wa': lambda v, c, m, p: obtener_estado_sesion(m.id)
    }

    def on_model_change(self, form, model, is_created):
        try:
            super().on_model_change(form, model, is_created)

            if is_created and not model.qr_code:
                db.session.flush()  # Para obtener el ID del modelo
                threading.Thread(target=llamar_a_webconnect_async, args=(model.id,)).start()
                flash("🔄 Solicitud enviada a webconnect en segundo plano para generar el QR.", "info")

        except IntegrityError as e:
            db.session.rollback()
            if 'tenants_telefono_key' in str(e):
                flash('⚠️ Ya existe un cliente con ese número de teléfono.', 'error')
            else:
                flash(f'⚠️ Error inesperado: {e}', 'error')
            raise


# 🔥 AGREGAR ModelViews separados para Servicio y Empleado
class ServicioModelView(SecureModelView):
    """Vista personalizada para gestionar servicios"""
    
    # Mantener form_overrides para WorkingHoursField
    form_overrides = {
        'working_hours': WorkingHoursField,
    }
    
    column_list = ('id', 'tenant', 'nombre', 'precio', 'duracion', 'es_informativo', 'solo_horas_exactas', 'turnos_consecutivos', 'calendar_id')
    column_searchable_list = ['nombre']
    column_filters = ['tenant.comercio', 'es_informativo', 'solo_horas_exactas', 'turnos_consecutivos']
    column_labels = {
        'tenant': 'Cliente/Comercio',
        'nombre': 'Nombre del Servicio',
        'precio': 'Precio ($)',
        'duracion': 'Duración (min)',
        'cantidad': 'Cantidad Disponible',
        'solo_horas_exactas': 'Solo Horas Exactas',
        'turnos_consecutivos': 'Turnos Consecutivos',  # 🆕 NUEVO LABEL
        'calendar_id': 'ID del Calendario',
        'working_hours': 'Horarios de Trabajo',
        'es_informativo': 'Es Informativo',
        'mensaje_personalizado': 'Mensaje Personalizado'
    }
    
    form_columns = [
        'tenant', 'nombre', 'precio', 'duracion', 'cantidad', 
        'solo_horas_exactas', 'turnos_consecutivos',  # 🆕 AGREGAR NUEVO CAMPO
        'calendar_id', 'working_hours',
        'es_informativo', 'mensaje_personalizado'
    ]
    
    form_widget_args = {
        'mensaje_personalizado': {
            'rows': 8,
            'placeholder': 'Mensaje que se mostrará cuando el cliente seleccione este servicio informativo. Ejemplo: "Para este servicio, contacta directamente al 099 123 456 o visítanos en nuestro local."'
        }
    }
    
    form_args = {
        'es_informativo': {
            'description': 'Si está marcado, este servicio no permitirá reservas y mostrará el mensaje personalizado.'
        },
        'mensaje_personalizado': {
            'description': 'Mensaje que se enviará al cliente cuando seleccione este servicio. Solo se usa si "Es Informativo" está marcado.'
        },
        'calendar_id': {
            'description': 'ID del calendario de Google. Solo necesario para servicios con reservas automáticas.'
        },
        'working_hours': {
            'description': 'Horarios de trabajo del servicio. Solo necesario para servicios con reservas automáticas.'
        },
        'solo_horas_exactas': {
            'description': 'Si está marcado, solo ofrecerá turnos en horas exactas (ej: 8:00, 9:00, 10:00). No compatible con turnos consecutivos.'
        },
        'turnos_consecutivos': {  # 🆕 NUEVA DESCRIPCIÓN
            'description': 'Si está marcado, ofrecerá turnos consecutivos sin solapamiento (ej: 8:00-9:30, 9:30-11:00). No compatible con solo horas exactas.'
        }
    }

    def scaffold_form(self):
        form_class = super().scaffold_form()
        form_class.tenant.query_factory = lambda: db.session.query(Tenant).order_by(Tenant.id)
        form_class.tenant.get_label = lambda obj: f"{obj.id} - {obj.nombre} ({obj.comercio})"
        return form_class

    def on_model_change(self, form, model, is_created):
        # Validación mejorada con manejo de errores
        try:
            es_informativo = getattr(model, 'es_informativo', False)
            
            if es_informativo:
                mensaje = getattr(model, 'mensaje_personalizado', '')
                if not mensaje or mensaje.strip() == "":
                    raise ValueError("Los servicios informativos deben tener un mensaje personalizado.")
                # Limpiar campos no necesarios para servicios informativos
                model.calendar_id = None
                model.working_hours = None
                model.solo_horas_exactas = False
                model.turnos_consecutivos = False
            else:
                # 🆕 VALIDACIÓN: solo_horas_exactas y turnos_consecutivos son mutuamente excluyentes
                solo_horas_exactas = getattr(model, 'solo_horas_exactas', False)
                turnos_consecutivos = getattr(model, 'turnos_consecutivos', False)
                
                if solo_horas_exactas and turnos_consecutivos:
                    raise ValueError("Un servicio no puede tener 'Solo Horas Exactas' y 'Turnos Consecutivos' activados al mismo tiempo. Elige una opción.")
                
                # Para servicios normales, validar que tengan configuración
                if not getattr(model, 'calendar_id', None) and not getattr(model, 'working_hours', None):
                    # Debe tener empleados asociados o configuración propia
                    from admin_app.models import Empleado
                    empleados = db.session.query(Empleado).filter_by(tenant_id=model.tenant_id).count()
                    if empleados == 0:
                        raise ValueError("Los servicios deben tener calendario propio O empleados disponibles en el sistema.")
            
            super().on_model_change(form, model, is_created)
            
        except Exception as e:
            db.session.rollback()
            raise ValueError(f"Error validando servicio: {str(e)}")

    # Formatter corregido como método estático
    @staticmethod
    def _es_informativo_formatter(view, context, model, name):
        try:
            es_informativo = getattr(model, 'es_informativo', False)
            if es_informativo:
                return Markup('<span style="color: blue; font-weight: bold;">ℹ️ Informativo</span>')
            else:
                return Markup('<span style="color: green;">📅 Con Reservas</span>')
        except Exception:
            return Markup('<span style="color: gray;">❓ Error</span>')
    
    # 🆕 NUEVO FORMATTER para turnos consecutivos
    @staticmethod
    def _turnos_consecutivos_formatter(view, context, model, name):
        try:
            turnos_consecutivos = getattr(model, 'turnos_consecutivos', False)
            solo_horas_exactas = getattr(model, 'solo_horas_exactas', False)
            
            if turnos_consecutivos:
                return Markup('<span style="color: purple; font-weight: bold;">🔗 Consecutivos</span>')
            elif solo_horas_exactas:
                return Markup('<span style="color: orange; font-weight: bold;">⏰ Solo Exactas</span>')
            else:
                return Markup('<span style="color: gray;">⚪ Normal</span>')
        except Exception:
            return Markup('<span style="color: gray;">❓ Error</span>')
    
    column_formatters = {
        'es_informativo': _es_informativo_formatter,
        'turnos_consecutivos': _turnos_consecutivos_formatter  # 🆕 NUEVO FORMATTER
    }


class EmpleadoModelView(SecureModelView):
    form_overrides = {
        'working_hours': WorkingHoursField,
    }
    column_list = ('id', 'nombre', 'tenant.comercio', 'calendar_id')
    form_columns = ('tenant', 'nombre', 'calendar_id', 'working_hours')
    column_labels = {'tenant.comercio': 'Cliente'}


class SecureAdminIndexView(AdminIndexView):
    @expose('/')
    def index(self):
        total_clientes = Tenant.query.count()
        ultimos_clientes = Tenant.query.order_by(Tenant.fecha_creada.desc()).limit(5).all()
        reservas = Reserva.query.order_by(Reserva.fecha_reserva.desc()).limit(20).all()
        errores = ErrorLog.query.order_by(ErrorLog.fecha.desc()).limit(10).all()
        total_errores = ErrorLog.query.count()
        estados = [r.estado for r in Reserva.query.all()]
        counter = Counter(estados)
        estados_reservas = list(counter.keys())
        cantidad_por_estado = list(counter.values())

        try:
            respuesta = requests.get(f"{WEBCONNECT_URL}/estado-sesiones", timeout=10)
            estado_sesiones = respuesta.json()
        except Exception as e:
            estado_sesiones = {"error": str(e)}

        # Consulta información de errores de sesión
        errores_sesion = {}
        try:
            respuesta_errores = requests.get(f"{WEBCONNECT_URL}/debug/errores", timeout=10)
            if respuesta_errores.ok:
                errores_sesion = respuesta_errores.json()
                print(f"✅ Errores de sesión obtenidos: {len(errores_sesion.get('session_errors', {}))} clientes con errores")
            else:
                print(f"⚠️ Error obteniendo errores de sesión: {respuesta_errores.status_code}")
        except Exception as e:
            print(f"⚠️ No se pudieron obtener errores de sesión: {e}")
            errores_sesion = {}

        return self.render('admin/custom_index.html',
                           total_clientes=total_clientes,
                           ultimos_clientes=ultimos_clientes,
                           reservas=reservas,
                           estados_reservas=estados_reservas,
                           cantidad_por_estado=cantidad_por_estado,
                           errores=errores,
                           total_errores=total_errores,
                           estado_sesiones=estado_sesiones,
                           errores_sesion=errores_sesion)

    def is_accessible(self):
        return basic_auth.authenticate()

    def inaccessible_callback(self, name, **kwargs):
        return basic_auth.challenge()

    @expose('/reiniciar/<int:cliente_id>')
    def reiniciar_cliente(self, cliente_id):
        # Elimina el QR viejo de la base antes de pedir uno nuevo
        tenant = Tenant.query.get(cliente_id)
        if tenant:
            tenant.qr_code = None
            db.session.commit()
        threading.Thread(target=regenerar_qr_webconnect_async, args=(cliente_id,)).start()
        flash(f"🔁 Regeneración de QR solicitada para cliente {cliente_id}.", "info")
        return redirect(request.referrer or url_for('admin.index'))

    @expose('/reset-errores/<int:cliente_id>')
    def reset_errores_cliente(self, cliente_id):
        try:
            webconnect_url = f"{WEBCONNECT_URL}/reset-errores/{cliente_id}"
            response = requests.post(webconnect_url, timeout=10)
            if response.ok:
                flash(f"✅ Errores reseteados para cliente {cliente_id}.", "success")
            else:
                flash(f"⚠️ No se pudieron resetear los errores: {response.status_code}", "warning")
        except Exception as e:
            flash(f"❌ Error al resetear errores: {e}", "error")
        return redirect(request.referrer or url_for('admin.index'))


class ReservaModelView(SecureModelView):
    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True
    column_searchable_list = ['cliente_nombre', 'cliente_telefono', 'empleado_nombre', 'servicio']
    column_filters = ['cliente_nombre', 'empleado_nombre', 'servicio', 'estado']
    column_list = ('id', 'fake_id', 'empresa', 'cliente_nombre', 'empleado_nombre', 'servicio', 'fecha_reserva', 'estado')
    form_columns = ('fake_id', 'empresa', 'cliente_nombre', 'empleado_nombre', 'servicio', 'fecha_reserva', 'estado')


class BlockedNumberModelView(SecureModelView):
    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    column_searchable_list = ['telefono']
    column_filters = ['empleado.nombre', 'cliente.comercio', 'telefono']
    column_list = ('id', 'telefono', 'empleado.nombre', 'cliente.comercio', 'fecha_bloqueo')
    column_labels = {
        'telefono': 'Teléfono',
        'empleado.nombre': 'Empleado',
        'cliente.comercio': 'Cliente/Comercio',
        'fecha_bloqueo': 'Fecha de Bloqueo'
    }
    form_columns = ('empleado', 'cliente', 'telefono')

    def scaffold_form(self):
        form_class = super().scaffold_form()
        # Personaliza el campo cliente para mostrar nombre y ID
        form_class.cliente.query_factory = lambda: db.session.query(Tenant).order_by(Tenant.id)
        form_class.cliente.get_label = lambda obj: f"{obj.id} - {obj.nombre} ({obj.comercio})"
        return form_class

    def on_model_change(self, form, model, is_created):
        # Solo validar si se selecciona empleado
        if model.empleado and model.cliente:
            if model.empleado.tenant_id != model.cliente.id:
                raise ValueError("El empleado seleccionado no pertenece al cliente/comercio seleccionado")
        super().on_model_change(form, model, is_created)
    


def init_admin(app, db):
    basic_auth.init_app(app)
    admin = Admin(
        app,
        name="Dashboard Clientes",
        index_view=SecureAdminIndexView(),
        template_mode="bootstrap4"
    )
    admin.add_view(TenantModelView(Tenant, db.session, name="Clientes"))
    admin.add_view(ServicioModelView(Servicio, db.session, name="Servicios"))  # 🔥 AGREGAR
    admin.add_view(EmpleadoModelView(Empleado, db.session, name="Empleados"))  # 🔥 AGREGAR
    admin.add_view(ReservaModelView(Reserva, db.session, name="Reservas"))
    admin.add_view(ErrorLogModelView(ErrorLog, db.session, name="Errores"))
    admin.add_view(BlockedNumberModelView(BlockedNumber, db.session, name="Números Bloqueados"))
    print("✅ Panel de administración inicializado")
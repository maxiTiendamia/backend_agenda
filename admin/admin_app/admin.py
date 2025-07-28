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

print("✅ Servicio:", Servicio.tenant.property.back_populates)

VENOM_URL = os.getenv("VENOM_URL", "http://195.26.250.62:3000")

basic_auth = BasicAuth()

# ⬇️ Nueva función para generar QR en segundo plano
def llamar_a_venom_async(cliente_id):
    try:
        venom_url = f"{VENOM_URL}/iniciar/{cliente_id}"
        print(f"🛠️ [Async] Enviando solicitud a Venom para generar QR del cliente {cliente_id}")
        response = requests.get(venom_url, timeout=10)
        if response.ok:
            print("✅ [Async] Venom generó QR correctamente")
        else:
            print(f"⚠️ [Async] Venom no respondió correctamente: {response.status_code}")
    except Exception as e:
        print(f"❌ [Async] Error al contactar a Venom: {e}")


# ⬇️ Nueva función para regenerar QR manualmente
def regenerar_qr_venom_async(cliente_id):
    try:
        venom_url = f"{VENOM_URL}/generar-qr/{cliente_id}"
        print(f"🔄 [Async] Regenerando QR para el cliente {cliente_id}")
        response = requests.post(venom_url, timeout=10)
        if response.ok:
            print("✅ [Async] QR regenerado correctamente")
        else:
            print(f"⚠️ [Async] Error al regenerar QR: {response.status_code}")
    except Exception as e:
        print(f"❌ [Async] Error al regenerar QR: {e}")


def obtener_estado_sesion(cliente_id):
    try:
        res = requests.get(f"{VENOM_URL}/estado-sesiones", timeout=10)
        sesiones = res.json()

        for sesion in sesiones:
            if str(sesion["clienteId"]) == str(cliente_id):
                estado = sesion["estado"]
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
                
                # Mostrar información adicional si está disponible
                info_extra = ""
                if "enMemoria" in sesion and sesion["enMemoria"]:
                    info_extra += " (En memoria)"
                if "tieneArchivos" in sesion and sesion["tieneArchivos"]:
                    info_extra += " (Con archivos)"
                
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
    form_overrides = {
        'working_hours_general': WorkingHoursField,  
    }

    inline_models = [
        (Servicio, dict(form_columns=['id', 'nombre', 'precio', 'duracion', 'cantidad','solo_horas_exactas'])), 
        (Empleado, dict(
            form_overrides={'working_hours': WorkingHoursField},
            form_columns=['id', 'nombre', 'calendar_id', 'working_hours']
        ))
    ]
    column_list = ('id', 'nombre', 'comercio', 'telefono', 'direccion', 'fecha_creada', 'qr_code', 'estado_wa')
    form_columns = (
    'nombre', 'apellido', 'comercio', 'telefono', 'direccion',
    'informacion_local', 'intervalo_entre_turnos',  
    'calendar_id_general', 'working_hours_general'
    )

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
                threading.Thread(target=llamar_a_venom_async, args=(model.id,)).start()
                flash("🔄 Solicitud enviada a Venom en segundo plano para generar el QR.", "info")

        except IntegrityError as e:
            db.session.rollback()
            if 'tenants_telefono_key' in str(e):
                flash('⚠️ Ya existe un cliente con ese número de teléfono.', 'error')
            else:
                flash(f'⚠️ Error inesperado: {e}', 'error')
            raise


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

        # Consulta al venom-service para estados de sesión
        try:
            respuesta = requests.get(f"{VENOM_URL}/estado-sesiones", timeout=10)
            estado_sesiones = respuesta.json()
        except Exception as e:
            estado_sesiones = {"error": str(e)}

        # Consulta información de errores de sesión
        errores_sesion = {}
        try:
            respuesta_errores = requests.get(f"{VENOM_URL}/debug/errores", timeout=10)
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
        threading.Thread(target=regenerar_qr_venom_async, args=(cliente_id,)).start()
        flash(f"🔁 Regeneración de QR solicitada para cliente {cliente_id}.", "info")
        return redirect(request.referrer or url_for('admin.index'))

    @expose('/reset-errores/<int:cliente_id>')
    def reset_errores_cliente(self, cliente_id):
        try:
            venom_url = f"{VENOM_URL}/reset-errores/{cliente_id}"
            response = requests.post(venom_url, timeout=10)
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
    admin.add_view(ReservaModelView(Reserva, db.session, name="Reservas"))
    admin.add_view(ErrorLogModelView(ErrorLog, db.session, name="Errores"))
    admin.add_view(BlockedNumberModelView(BlockedNumber, db.session, name="Números Bloqueados"))
    print("✅ Panel de administración inicializado")
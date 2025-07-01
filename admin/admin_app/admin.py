from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_basicauth import BasicAuth
from flask import render_template, flash, Markup
from wtforms import Field
from admin_app.models import Tenant, Empleado, Servicio, Reserva, ErrorLog
from admin_app.database import db
import json
from sqlalchemy.exc import IntegrityError
from collections import Counter
import os

print("✅ Servicio:", Servicio.tenant.property.back_populates)

basic_auth = BasicAuth()

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

class TenantModelView(SecureModelView):
    form_overrides = {'working_hours': WorkingHoursField}
    inline_models = [
        (Servicio, dict(form_columns=['id', 'nombre', 'precio', 'duracion'])),
        (Empleado, dict(
            form_overrides={'working_hours': WorkingHoursField},
            form_columns=['id', 'nombre', 'calendar_id', 'working_hours']
        ))
    ]
    column_list = ('id', 'nombre', 'comercio', 'telefono', 'direccion', 'fecha_creada', 'qr_code')
    form_columns = (
        'nombre', 'apellido', 'comercio', 'telefono', 'direccion', 'phone_number_id'
    )

    column_formatters = {
        'qr_code': lambda v, c, m, p: Markup(f"<img src='data:image/png;base64,{m.qr_code}' style='height:150px;'>") if m.qr_code else ''
    }

    def on_model_change(self, form, model, is_created):
        try:
            super().on_model_change(form, model, is_created)
            if is_created:
                from admin_app.utils.venom_qr import generar_qr_para_cliente
                generar_qr_para_cliente(model.telefono)
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
        return self.render('admin/custom_index.html',
                           total_clientes=total_clientes,
                           ultimos_clientes=ultimos_clientes,
                           reservas=reservas,
                           estados_reservas=estados_reservas,
                           cantidad_por_estado=cantidad_por_estado,
                           errores=errores,
                           total_errores=total_errores)

    def is_accessible(self):
        return basic_auth.authenticate()

    def inaccessible_callback(self, name, **kwargs):
        return basic_auth.challenge()

class ReservaModelView(SecureModelView):
    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = True
    column_searchable_list = ['cliente_nombre', 'cliente_telefono', 'empleado_nombre', 'servicio']
    column_filters = ['cliente_nombre', 'empleado_nombre', 'servicio', 'estado']
    column_list = ('id', 'fake_id', 'empresa', 'cliente_nombre', 'empleado_nombre', 'servicio', 'fecha_reserva', 'estado')
    form_columns = ('fake_id', 'empresa', 'cliente_nombre', 'empleado_nombre', 'servicio', 'fecha_reserva', 'estado')

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
    print("✅ Panel de administración inicializado")
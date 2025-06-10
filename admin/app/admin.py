from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_basicauth import BasicAuth
from flask import render_template
from wtforms import Field
from app.models import Tenant
from app.database import db
import json

basic_auth = BasicAuth()

# Widget personalizado para editar horarios laborales
class WorkingHoursWidget:
    def __call__(self, field, **kwargs):
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        existing = json.loads(field.data or '{}')
        html = "<div style='padding: 1rem 0;'>"
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

    def process(self, formdata, data=None):
        self.formdata = formdata
        self.data = data

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


class SecureModelView(ModelView):
    def is_accessible(self):
        return basic_auth.authenticate()

    def inaccessible_callback(self, name, **kwargs):
        return basic_auth.challenge()


class TenantModelView(SecureModelView):
    form_overrides = {'working_hours': WorkingHoursField}
    column_list = ('id', 'nombre', 'comercio', 'telefono', 'fecha_creada')
    form_columns = (
        'nombre', 'apellido', 'comercio', 'telefono',
        'calendar_id', 'phone_number_id', 'verify_token',
        'access_token', 'working_hours'
    )


class SecureAdminIndexView(AdminIndexView):
    @expose('/')
    def index(self):
        total_clientes = Tenant.query.count()
        ultimos_clientes = Tenant.query.order_by(Tenant.fecha_creada.desc()).limit(5).all()
        return self.render('admin/custom_index.html',
                           total_clientes=total_clientes,
                           ultimos_clientes=ultimos_clientes)

    def is_accessible(self):
        return basic_auth.authenticate()

    def inaccessible_callback(self, name, **kwargs):
        return basic_auth.challenge()


def init_admin(app, db):
    basic_auth.init_app(app)
    admin = Admin(
        app,
        name="Dashboard Clientes",
        index_view=SecureAdminIndexView(),
        template_mode="bootstrap4"
    )
    admin.add_view(TenantModelView(Tenant, db.session, name="Clientes"))
    print("✅ Panel de administración inicializado")
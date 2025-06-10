from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_basicauth import BasicAuth
from flask import render_template
from app.models import Tenant
from app.database import db

basic_auth = BasicAuth()

# Vista protegida
class SecureModelView(ModelView):
    def is_accessible(self):
        return basic_auth.authenticate()

    def inaccessible_callback(self, name, **kwargs):
        return basic_auth.challenge()

# Vista cliente con todos los campos
class TenantModelView(SecureModelView):
    column_list = ('id', 'nombre', 'comercio', 'telefono', 'fecha_creada')
    form_columns = ('nombre', 'apellido', 'comercio', 'telefono',
                    'calendar_id', 'phone_number_id', 'verify_token', 'access_token')

# Dashboard de inicio
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

# Inicialización
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
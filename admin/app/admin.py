from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_basicauth import BasicAuth
from wtforms import TextAreaField
from wtforms_sqlalchemy.fields import QuerySelectField
from app.models import Tenant, TenantConfig
from app.database import db
from flask import render_template_string

basic_auth = BasicAuth()

# Vista segura base
class SecureModelView(ModelView):
    def is_accessible(self):
        return basic_auth.authenticate()
    def inaccessible_callback(self, name, **kwargs):
        return basic_auth.challenge()

# Vista con relación a Tenant
class SecureModelViewWithTenant(SecureModelView):
    form_overrides = dict(
        tenant_id=QuerySelectField
    )
    form_args = dict(
        tenant_id=dict(
            label="Cliente",
            query_factory=lambda: Tenant.query.all(),
            get_label="nombre"
        )
    )
    column_list = ('id', 'tenant_id', 'business_hours', 'calendar_id', 'phone_number_id', 'verify_token', 'access_token')

# Página principal personalizada
class SecureAdminIndexView(AdminIndexView):
    @expose('/')
    def index(self):
        total_clientes = Tenant.query.count()
        return render_template_string("""
            <div class="container" style="padding:2rem">
              <h2>📊 Resumen general</h2>
              <p><b>Total de clientes registrados:</b> {{ total }}</p>
              <p>Bienvenido/a al panel de administración. Aquí podrás gestionar los datos de tus clientes y sus agendas.</p>
            </div>
        """, total=total_clientes)

    def is_accessible(self):
        return basic_auth.authenticate()

    def inaccessible_callback(self, name, **kwargs):
        return basic_auth.challenge()

# Inicialización del panel admin
def init_admin(app, db):
    basic_auth.init_app(app)
    admin = Admin(
        app,
        name="Dashboard Clientes",
        index_view=SecureAdminIndexView(),
        template_mode="bootstrap4"
    )
    admin.add_view(SecureModelView(Tenant, db.session, name="Clientes"))
    admin.add_view(SecureModelViewWithTenant(TenantConfig, db.session, name="Configuraciones"))
    print("✅ Panel de administración inicializado")
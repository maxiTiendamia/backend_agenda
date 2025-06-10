from flask_admin import Admin, AdminIndexView
from flask_admin.contrib.sqla import ModelView
from flask_basicauth import BasicAuth
from wtforms import TextAreaField
from wtforms_sqlalchemy.fields import QuerySelectField
from app.models import Tenant

basic_auth = BasicAuth()

# Vista segura para modelos normales
class SecureModelView(ModelView):
    def is_accessible(self):
        return basic_auth.authenticate()
    def inaccessible_callback(self, name, **kwargs):
        return basic_auth.challenge()

# Vista segura con selector de Tenant
from models import Tenant  # Importa Tenant aquí para usarlo en el query_factory

class SecureModelViewWithTenant(SecureModelView):
    form_overrides = dict(
        tenant_id=QuerySelectField
    )
    form_args = dict(
        tenant_id=dict(
            label="Tenant",
            query_factory=lambda: Tenant.query.all(),
            get_label="nombre"
        )
    )
    column_list = ('id', 'tenant_id', 'business_hours', 'calendar_id', 'phone_number_id', 'verify_token', 'access_token')

# Vista segura con TextArea para campos grandes
class SecureModelViewWithTextArea(SecureModelView):
    form_overrides = {
        'business_hours': TextAreaField,
        'google_service_account_info': TextAreaField
    }
    form_widget_args = {
        'business_hours': {'rows': 5, 'style': 'width: 500px;'},
        'google_service_account_info': {'rows': 10, 'style': 'width: 500px;'}
    }
    column_exclude_list = ['google_service_account_info']

class SecureAdminIndexView(AdminIndexView):
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
    from models import Tenant, TenantConfig, TenantCredentials
    admin.add_view(ModelView(Tenant, db.session))  # o SecureModelView si quieres seguridad
    admin.add_view(SecureModelViewWithTenant(TenantConfig, db.session))
    admin.add_view(SecureModelViewWithTenant(TenantCredentials, db.session))
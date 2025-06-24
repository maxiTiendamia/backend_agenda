from flask import Flask
from admin_app.database import init_db, db
from admin_app.admin import init_admin
import os

# Especificar carpeta de templates
template_dir = os.path.join(os.path.dirname(__file__), 'admin_app', 'templates')
app = Flask(__name__, template_folder=template_dir)

# Configuración base
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'admin-secret')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['BASIC_AUTH_USERNAME'] = os.getenv("ADMIN_USER")
app.config['BASIC_AUTH_PASSWORD'] = os.getenv("ADMIN_PASSWORD")

# Inicializar DB y Admin
init_db(app)
init_admin(app, db)

@app.route("/")
def index():
    return "✅ Panel admin funcionando"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
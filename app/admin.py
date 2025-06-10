from fastapi import FastAPI
from flask import Flask
from starlette.middleware.wsgi import WSGIMiddleware
from app.database import init_db, db
from app.admin import init_admin
from app.whatsapp_routes import router as whatsapp_router
import os

# Crear instancia Flask (para admin)
flask_app = Flask(__name__)
flask_app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'admin-secret')
flask_app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
flask_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inicializar base de datos y panel admin
init_db(flask_app)
init_admin(flask_app, db)

# Ruta de prueba para confirmar que Flask está montado
@flask_app.route("/test")
def test_route():
    return "✅ Flask funciona correctamente"

# Crear instancia FastAPI
app = FastAPI()
app.include_router(whatsapp_router)

# Montar Flask sobre FastAPI
app.mount("/admin", WSGIMiddleware(flask_app))

@app.get("/")
def root():
    return {"status": "ok"}
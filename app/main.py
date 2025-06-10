from fastapi import FastAPI
from app.whatsapp_routes import router as whatsapp_router
from app.database import init_db
from app.admin import init_admin
from flask import Flask
import os

# Crear instancia de Flask para admin y DB
flask_app = Flask(__name__)
flask_app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'admin-secret')
flask_app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
flask_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

init_db(flask_app)
init_admin(flask_app)

# Crear instancia FastAPI
app = FastAPI()
app.include_router(whatsapp_router)

@app.get("/")
def root():
    return {"status": "ok"}

# Ejecutar admin si se usa localmente
if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
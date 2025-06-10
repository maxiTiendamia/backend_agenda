from fastapi import FastAPI
from app.whatsapp_routes import router as whatsapp_router
from app.database import init_db, db
from app.admin import init_admin
from flask import Flask
from starlette.middleware.wsgi import WSGIMiddleware
import os

# Instancia de Flask
flask_app = Flask(__name__)
flask_app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'admin-secret')
flask_app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
flask_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

init_db(flask_app)
init_admin(flask_app, db)

# Instancia de FastAPI
app = FastAPI()
app.include_router(whatsapp_router)

# Montar Flask dentro de FastAPI
app.mount("/admin", WSGIMiddleware(flask_app))

@app.get("/")
def root():
    return {"status": "ok"}
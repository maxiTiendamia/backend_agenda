from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# 🔹 Para uso dentro de la app Flask
db = SQLAlchemy()
migrate = Migrate()

# 🔹 Agregado para uso externo a la app Flask (ej: scripts, tareas, utils)
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy import create_engine
import os

DATABASE_URL = os.getenv("DATABASE_URL")

# 🔹 Sesión independiente (por fuera del contexto de Flask)
engine = create_engine(DATABASE_URL)
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

def init_db(app):
    db.init_app(app)
    migrate.init_app(app, db)
    with app.app_context():
        db.create_all()
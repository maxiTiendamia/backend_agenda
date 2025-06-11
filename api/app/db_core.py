from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import os

DB_URL = os.getenv('DATABASE_URL', 'postgresql://reservas_user:reservas_pass@localhost:5432/reservas_db')
engine = create_engine(DB_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
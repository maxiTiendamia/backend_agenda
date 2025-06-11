from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

import os
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./test.db")

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}  # ← solo si usás SQLite
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

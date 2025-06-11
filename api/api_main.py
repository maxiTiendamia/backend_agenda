import sys
import os
sys.path.append(os.path.dirname(__file__))

from fastapi import FastAPI
from app.database import init_db, db
from app.models import Tenant
from app.whatsapp_routes import router as whatsapp_router
from starlette.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS (opcional)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(whatsapp_router)

@app.get("/")
def root():
    return {"status": "API funcionando"}

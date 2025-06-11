from fastapi import FastAPI
from utils import init_db, db
from utils import Tenant
from utils import router as whatsapp_router
from starlette.middleware.cors import CORSMiddleware
from flask import Flask

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

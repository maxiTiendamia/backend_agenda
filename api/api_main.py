from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import sys
from dotenv import load_dotenv

# Agregar el directorio actual al path para imports relativos
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Cargar variables de entorno
load_dotenv()

# 🔥 CORREGIR IMPORTS - usar imports relativos desde la carpeta actual
from app.whatsapp_routes import router as whatsapp_router

app = FastAPI(
    title="Backend Agenda API",
    description="API para sistema de reservas con WhatsApp",
    version="1.0.0"
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir routers
app.include_router(whatsapp_router, prefix="/api/whatsapp", tags=["whatsapp"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])

@app.get("/")
async def root():
    return {"message": "Backend Agenda API funcionando correctamente"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "API funcionando"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

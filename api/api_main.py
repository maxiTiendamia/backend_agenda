from fastapi import FastAPI
from app.whatsapp_routes import router as whatsapp_router

app = FastAPI()
app.include_router(whatsapp_router)

@app.get("/")
def root():
    return {"status": "API operativa"}
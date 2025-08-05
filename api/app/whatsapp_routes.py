from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import redis
import os
import sys
from datetime import datetime, timezone

# 🔥 AJUSTAR PATH PARA AI_SERVICES
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir))
sys.path.insert(0, parent_dir)

from app.deps import get_db
from app.models import ErrorLog
from api.ai_services.ai_conversation_manager import AIConversationManager

# Configuración
REDIS_URL = os.getenv("REDIS_URL", "rediss://default:AcOQAAIjcDEzOGI2OWU1MzYxZDQ0YWQ2YWU3ODJlNWNmMGY5MjIzY3AxMA@literate-toucan-50064.upstash.io:6379")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# Inicializar IA Manager
ai_manager = AIConversationManager(
    api_key=os.getenv("OPENAI_API_KEY"),
    redis_client=redis_client
)

router = APIRouter()

@router.post("/webhook")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    """
    🤖 WEBHOOK COMPLETAMENTE MANEJADO POR IA
    """
    try:
        data = await request.json()
        telefono = data.get("telefono", "").replace('@c.us', '')
        mensaje = data.get("mensaje", "").strip()
        cliente_id = int(data.get("cliente_id"))
        
        print(f"🤖 [IA] Cliente {cliente_id} | {telefono} -> {mensaje}")
        
        # IA procesa TODA la conversación
        respuesta = await ai_manager.process_message(
            telefono=telefono,
            mensaje=mensaje,
            cliente_id=cliente_id,
            db=db
        )
        
        if respuesta:
            print(f"🤖 [IA] Respuesta: {respuesta[:100]}...")
        
        return JSONResponse(content={"mensaje": respuesta})
        
    except ValueError as e:
        error_msg = str(e)
        if "offset-naive and offset-aware" in error_msg:
            print(f"❌ Error de timezone en IA: {e}")
            return JSONResponse(content={
                "mensaje": "❌ Hay un problema temporal con el sistema. Intenta de nuevo en un momento."
            })
        elif "can't compare offset-naive and offset-aware datetimes" in error_msg:
            print(f"❌ Error de comparación de fechas: {e}")
            return JSONResponse(content={
                "mensaje": "❌ Problema con las fechas del sistema. Contacta soporte."
            })
        raise e
        
    except Exception as e:
        print(f"❌ Error en webhook IA: {e}")
        
        # Log del error con fecha UTC consistente
        try:
            if 'data' in locals():
                error_log = ErrorLog(
                    cliente=str(data.get("cliente_id", "Unknown")),
                    telefono=data.get("telefono", "Unknown"), 
                    mensaje=data.get("mensaje", "Unknown"),
                    error=str(e),
                    fecha=datetime.now(timezone.utc)  # 🔧 Asegurar UTC
                )
                db.add(error_log)
                db.commit()
        except Exception as log_error:
            print(f"❌ Error guardando log: {log_error}")
        
        return JSONResponse(content={
            "mensaje": "❌ Tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo en unos minutos?"
        })
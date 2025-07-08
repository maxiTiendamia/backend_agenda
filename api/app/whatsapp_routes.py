from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session
from api.app.models import Tenant, Servicio, Empleado, Reserva, ErrorLog, BlockedNumber
from api.app.deps import get_db
from api.utils.calendar_utils import get_available_slots, create_event, cancelar_evento_google
from api.utils.generador_fake_id import generar_fake_id
import time
import re
import traceback
import os
import pytz
import redis
import json
import httpx
from datetime import datetime, timedelta, timezone

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
VENOM_URL = os.getenv("VENOM_URL", "https://backend-agenda-us92.onrender.com")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
SESSION_TTL = 300  # segundos
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

def set_user_state(user_id, state):
    try:
        redis_client.setex(
            f"user_state:{user_id}",
            SESSION_TTL,
            json.dumps(state, cls=DateTimeEncoder)
        )
    except Exception as e:
        print(f"⚠️ Error guardando estado en Redis: {e}")

def get_user_state(user_id):
    try:
        state_json = redis_client.get(f"user_state:{user_id}")
        return json.loads(state_json) if state_json else None
    except Exception as e:
        print(f"⚠️ Error leyendo estado de Redis: {e}")
        return None

async def notificar_chat_humano_completo(cliente_id: int, telefono: str, mensaje: str):
    """Enviar notificación completa cuando se requiere atención humana"""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{VENOM_URL}/notificar-chat-humano",
                json={
                    "cliente_id": cliente_id,
                    "telefono": telefono,
                    "mensaje": mensaje,
                    "tipo": "solicitud_ayuda"
                },
                timeout=5.0
            )
        print(f"✅ Notificación enviada - Cliente {cliente_id} solicita ayuda: {telefono}")
    except Exception as e:
        print(f"⚠️ Error enviando notificación de ayuda: {e}")

router = APIRouter()

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")

@router.post("/webhook")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
        telefono = data.get("telefono")
        mensaje = data.get("mensaje", "").strip().lower()
        cliente_id = data.get("cliente_id")

        # Validar que cliente_id sea un entero
        try:
            cliente_id = int(cliente_id)
        except (TypeError, ValueError):
            return JSONResponse(content={"mensaje": "❌ Error: cliente_id inválido."}, status_code=400)

        tenant = db.query(Tenant).filter_by(id=cliente_id).first()
        if not tenant:
            return JSONResponse(content={"mensaje": "⚠️ Cliente no encontrado."})

        # --- BLOQUEO DE NÚMEROS ---
        empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
        empleados_ids = [e.id for e in empleados]
        bloqueado = db.query(BlockedNumber).filter(
            (BlockedNumber.telefono == telefono) &
            (BlockedNumber.empleado_id.in_(empleados_ids)) &
            (BlockedNumber.cliente_id == tenant.id)
        ).first() if empleados_ids else False
        if bloqueado:
            return JSONResponse(content={"mensaje": ""}, status_code=200)

        now = time.time()
        state = get_user_state(telefono)
        
        # Si no hay estado previo o es muy antiguo, crear estado inicial
        if not state or now - state.get("last_interaction", 0) > SESSION_TTL:
            state = {"step": "welcome", "last_interaction": now, "mode": "bot", "is_first_contact": True}
        else:
            state["last_interaction"] = now

        # --- MANEJO DE MODO HUMANO ---
        # Si el usuario está en modo humano, solo responder a comandos específicos
        if state.get("mode") == "human":
            if mensaje in ["bot", "volver", "Bot", "VOLVER", "BOT"]:
                state["mode"] = "bot"
                state["step"] = "welcome"
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": "🤖 El asistente virtual está activo nuevamente. Escribe \"Turno\" para agendar."})
            else:
                # Usuario sigue en modo humano, reenviar mensaje al asesor
                try:
                    import asyncio
                    asyncio.create_task(notificar_chat_humano_completo(tenant.id, telefono, mensaje))
                except Exception as e:
                    print(f"⚠️ Error enviando notificación: {e}")
                # NO actualizar estado aquí para mantener el modo humano
                return JSONResponse(content={"mensaje": ""})  # Respuesta vacía para no confundir

        # --- SOLICITUD DE AYUDA ---
        # Verificar si solicita ayuda ANTES de cualquier otra lógica
        if "ayuda" in mensaje:
            state["mode"] = "human"
            state["step"] = "human_mode"
            set_user_state(telefono, state)
            # Solo notificar al venom-service
            try:
                import asyncio
                asyncio.create_task(notificar_chat_humano_completo(tenant.id, telefono, mensaje))
            except Exception as e:
                print(f"⚠️ Error enviando notificación: {e}")
            return JSONResponse(content={"mensaje": "🚪 Un asesor te responderá a la brevedad. Puedes escribir \"Bot\" y volveré a ayudarte 😊"})

        # Actualizar estado solo si NO está en modo humano
        set_user_state(telefono, state)

        # --- MENSAJES DE DESPEDIDA ---
        if any(x in mensaje for x in ["gracias", "chau", "chao", "nos vemos"]):
            return JSONResponse(content={"mensaje": "😊 ¡Gracias por tu mensaje! Que tengas un buen día!"})

        if re.match(r"^cancelar\s+\w+", mensaje):
            partes = mensaje.strip().split(maxsplit=1)
            if len(partes) < 2:
                return JSONResponse(content={"mensaje": "❌ Debes escribir: cancelar + código"})
            fake_id = partes[1].strip().upper()
            try:
                reserva = db.query(Reserva).filter_by(fake_id=fake_id).first()
                if not reserva:
                    return JSONResponse(content={"mensaje": "❌ No se encontró la reserva. Verifica el código."})
                exito = cancelar_evento_google(
                    calendar_id=reserva.empleado_calendar_id,
                    reserva_id=reserva.event_id,
                    service_account_info=GOOGLE_CREDENTIALS_JSON
                )
                if exito:
                    reserva.estado = "cancelado"
                    db.commit()
                    state.clear()
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": "✅ Tu turno fue cancelado correctamente."})
                else:
                    return JSONResponse(content={"mensaje": "❌ No se pudo cancelar el turno. Intenta más tarde."})
            except Exception as e:
                print("❌ Error al cancelar turno:", e)
                return JSONResponse(content={"mensaje": "❌ No se pudo cancelar el turno. Intenta más tarde."})

        if state.get("step") == "welcome":
            # Si es primer contacto, siempre enviar mensaje inicial sin importar qué escriba
            if state.get("is_first_contact"):
                state["is_first_contact"] = False  # Marcar que ya no es primer contacto
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": f"¡Hola! 👋 Bienvenido/a a *{tenant.comercio}*\n\n🕐 *Horarios de atención:*\nLunes a Viernes: 9:00 - 18:00\nSábados: 9:00 - 13:00\n\n📅 Para agendar una cita, escribe:\n• \"Turno\" o \"Reservar\"\n• Tu nombre completo\n• Servicio que necesitas\n• Día y horario preferido\n\n💬 Si necesitas ayuda personalizada, escribe \"Ayuda\"\n\n¿En qué podemos ayudarte hoy?"})
            
            # Para contactos posteriores, procesar normalmente
            if "turno" in mensaje or "reservar" in mensaje or "agendar" in mensaje:
                servicios = tenant.servicios
                if not servicios:
                    return JSONResponse(content={"mensaje": "⚠️ No hay servicios disponibles."})
                msg = "¿Qué servicio deseas reservar?\n"
                for i, s in enumerate(servicios, 1):
                    msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                msg += "\nResponde con el número del servicio."
                state["step"] = "waiting_servicio"
                state["servicios"] = [s.id for s in servicios]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})
            elif "informacion" in mensaje or "info" in mensaje:
                if tenant.informacion_local:
                    state["step"] = "after_info"
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": f"{tenant.informacion_local}\n\n¿Qué deseas hacer?\n🔹 Escribe \"Turno\" para agendar\n🔹 Escribe \"Ayuda\" para hablar con un asesor"})
                else:
                    return JSONResponse(content={"mensaje": "⚠️ No hay información disponible en este momento."})
            else:
                # Usuario escribió algo que no entendemos en el paso welcome
                return JSONResponse(content={"mensaje": "🤔 No entiendo lo que necesitas.\n\n¿Qué deseas hacer?\n🔹 Escribe \"Turno\" para agendar una cita\n🔹 Escribe \"Información\" para conocer más\n🔹 Escribe \"Ayuda\" para hablar con un asesor"})

        # --- MANEJO DE USUARIOS EN CONVERSACIÓN ---
        # Si el usuario ya recibió la bienvenida pero escribe algo que no entendemos
        if state.get("step") == "welcome" and not state.get("is_first_contact"):
            if "turno" in mensaje or "reservar" in mensaje or "agendar" in mensaje:
                servicios = tenant.servicios
                if not servicios:
                    return JSONResponse(content={"mensaje": "⚠️ No hay servicios disponibles."})
                msg = "¿Qué servicio deseas reservar?\n"
                for i, s in enumerate(servicios, 1):
                    msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                msg += "\nResponde con el número del servicio."
                state["step"] = "waiting_servicio"
                state["servicios"] = [s.id for s in servicios]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})
            elif "informacion" in mensaje or "info" in mensaje:
                if tenant.informacion_local:
                    state["step"] = "after_info"
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": f"{tenant.informacion_local}\n\n¿Qué deseas hacer?\n🔹 Escribe \"Turno\" para agendar\n🔹 Escribe \"Ayuda\" para hablar con un asesor"})
                else:
                    return JSONResponse(content={"mensaje": "⚠️ No hay información disponible en este momento."})
            else:
                # Usuario en conversación escribió algo que no entendemos
                return JSONResponse(content={"mensaje": "🤔 No entiendo lo que necesitas.\n\n¿Qué deseas hacer?\n🔹 Escribe \"Turno\" para agendar una cita\n🔹 Escribe \"Información\" para conocer más\n🔹 Escribe \"Ayuda\" para hablar con un asesor"})

        if state.get("step") == "after_info":
            if "turno" in mensaje:
                servicios = tenant.servicios
                if not servicios:
                    return JSONResponse(content={"mensaje": "⚠️ No hay servicios disponibles."})
                msg = "¿Qué servicio deseas reservar?\n"
                for i, s in enumerate(servicios, 1):
                    msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                msg += "\nResponde con el número del servicio."
                state["step"] = "waiting_servicio"
                state["servicios"] = [s.id for s in servicios]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})
            else:
                return JSONResponse(content={"mensaje": "¿Qué deseas hacer?\n🔹 Escribe \"Turno\" para agendar\n🔹 Escribe \"Ayuda\" para hablar con un asesor"})

        if state.get("step") == "waiting_servicio":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                servicios_ids = state.get("servicios", [])
                if 0 <= idx < len(servicios_ids):
                    servicio_id = servicios_ids[idx]
                    servicio = db.query(Servicio).get(servicio_id)
                    empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                    if not empleados:
                        return JSONResponse(content={"mensaje": "⚠️ No hay empleados disponibles."})
                    msg = f"¿Con qué empleado?\n"
                    for i, e in enumerate(empleados, 1):
                        msg += f"🔹{i}. {e.nombre}\n"
                    msg += "\nResponde con el número del empleado."
                    state["step"] = "waiting_empleado"
                    state["servicio_id"] = servicio_id
                    state["empleados"] = [e.id for e in empleados]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                else:
                    servicios = tenant.servicios
                    msg = "❌ Opción inválida.\n¿Qué servicio deseas reservar?\n"
                    for i, s in enumerate(servicios, 1):
                        msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                    msg += "\nResponde con el número del servicio."
                    state["step"] = "waiting_servicio"
                    state["servicios"] = [s.id for s in servicios]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
            else:
                servicios = tenant.servicios
                msg = "❌ Opción inválida.\n¿Qué servicio deseas reservar?\n"
                for i, s in enumerate(servicios, 1):
                    msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
                msg += "\nResponde con el número del servicio."
                state["step"] = "waiting_servicio"
                state["servicios"] = [s.id for s in servicios]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})

        if state.get("step") == "waiting_empleado":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                empleados_ids = state.get("empleados", [])
                if 0 <= idx < len(empleados_ids):
                    empleado_id = empleados_ids[idx]
                    empleado = db.query(Empleado).get(empleado_id)
                    servicio = db.query(Servicio).get(state["servicio_id"])
                    slots = get_available_slots(
                        calendar_id=empleado.calendar_id,
                        credentials_json=GOOGLE_CREDENTIALS_JSON,
                        working_hours_json=empleado.working_hours,
                        service_duration=servicio.duracion,    
                        intervalo_entre_turnos=20,             
                        max_turnos=25
                    )
                    ahora = datetime.now(pytz.timezone("America/Montevideo"))
                    slots_futuros = [s for s in slots if s > ahora]
                    max_turnos = 25
                    slots_mostrar = slots_futuros[:max_turnos]
                    if not slots_mostrar:
                        return JSONResponse(content={"mensaje": "⚠️ No hay turnos disponibles para este empleado."})
                    msg = "📅 Estos son los próximos turnos disponibles:\n"
                    for i, slot in enumerate(slots_mostrar, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    state["step"] = "waiting_turno_final"
                    state["empleado_id"] = empleado_id
                    state["slots"] = [s.isoformat() for s in slots_mostrar]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
                else:
                    empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                    msg = "❌ Opción inválida.\n¿Con qué empleado?\n"
                    for i, e in enumerate(empleados, 1):
                        msg += f"🔹{i}. {e.nombre}\n"
                    msg += "\nResponde con el número del empleado."
                    state["step"] = "waiting_empleado"
                    state["empleados"] = [e.id for e in empleados]
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
            else:
                empleados = db.query(Empleado).filter_by(tenant_id=tenant.id).all()
                msg = "❌ Opción inválida.\n¿Con qué empleado?\n"
                for i, e in enumerate(empleados, 1):
                    msg += f"🔹{i}. {e.nombre}\n"
                msg += "\nResponde con el número del empleado."
                state["step"] = "waiting_empleado"
                state["empleados"] = [e.id for e in empleados]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})

        if state.get("step") == "waiting_turno_final":
            if mensaje.isdigit():
                idx = int(mensaje) - 1
                slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                if 0 <= idx < len(slots):
                    slot = slots[idx]
                    empleado = db.query(Empleado).get(state["empleado_id"])
                    servicio = db.query(Servicio).get(state["servicio_id"])
                    state["slot"] = slot.isoformat()
                    state["empleado_id"] = empleado.id
                    state["servicio_id"] = servicio.id
                    state["step"] = "waiting_nombre"
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": "Por favor, escribe tu nombre y apellido para confirmar la reserva."})
                else:
                    slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                    msg = "❌ Opción inválida.\n📅 Estos son los próximos turnos disponibles:\n"
                    for i, slot in enumerate(slots, 1):
                        msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                    msg += "\nResponde con el número del turno."
                    state["step"] = "waiting_turno_final"
                    set_user_state(telefono, state)
                    return JSONResponse(content={"mensaje": msg})
            else:
                slots = [datetime.fromisoformat(s) if isinstance(s, str) else s for s in state.get("slots", [])]
                msg = "❌ Opción inválida.\n📅 Estos son los próximos turnos disponibles:\n"
                for i, slot in enumerate(slots, 1):
                    msg += f"🔹{i}. {slot.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                state["step"] = "waiting_turno_final"
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})

        elif state.get("step") == "waiting_nombre":
            nombre_apellido = mensaje.strip().title()
            slot = state.get("slot")
            if isinstance(slot, str):
                slot = datetime.fromisoformat(slot)
            empleado = db.query(Empleado).get(state["empleado_id"])
            servicio = db.query(Servicio).get(state["servicio_id"])

            # Verifica disponibilidad
            from api.utils.calendar_utils import build_service
            service = build_service(GOOGLE_CREDENTIALS_JSON)
            start_time = slot.isoformat()
            end_time = (slot + timedelta(minutes=servicio.duracion)).isoformat()
            events_result = service.events().list(
                calendarId=empleado.calendar_id,
                timeMin=start_time,
                timeMax=end_time,
                singleEvents=True
            ).execute()
            events = events_result.get('items', [])
            if events:
                slots_actuales = get_available_slots(
                    calendar_id=empleado.calendar_id,
                    credentials_json=GOOGLE_CREDENTIALS_JSON,
                    working_hours_json=empleado.working_hours,
                    service_duration=servicio.duracion,
                    intervalo_entre_turnos=20,
                    max_turnos=10
                )
                msg = "❌ El turno seleccionado ya no está disponible. Por favor, elige otro:\n"
                for i, s in enumerate(slots_actuales, 1):
                    msg += f"🔹{i}. {s.strftime('%d/%m %H:%M')}\n"
                msg += "\nResponde con el número del turno."
                state["step"] = "waiting_turno_final"
                state["slots"] = [s.isoformat() for s in slots_actuales]
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": msg})

            # Crear evento en Google Calendar
            event_id = create_event(
                calendar_id=empleado.calendar_id,
                slot_dt=slot,
                user_phone=telefono,
                service_account_info=GOOGLE_CREDENTIALS_JSON,
                duration_minutes=servicio.duracion,
                client_service=f"Cliente: {nombre_apellido} - Tel: {telefono} - Servicio: {servicio.nombre}"
            )
            fake_id = generar_fake_id()
            reserva = Reserva(
                fake_id=fake_id,
                event_id=event_id,
                empresa=tenant.comercio,
                empleado_id=empleado.id,
                empleado_nombre=empleado.nombre,
                empleado_calendar_id=empleado.calendar_id,
                cliente_nombre=nombre_apellido,
                cliente_telefono=telefono,
                servicio=servicio.nombre,
                estado="activo"
            )
            db.add(reserva)
            db.commit()
            state.clear()
            set_user_state(telefono, state)
            return JSONResponse(content={"mensaje": (
                f"✅ {nombre_apellido}, tu turno fue reservado con éxito para el {slot.strftime('%d/%m %H:%M')} con {empleado.nombre}.\n"
                f"\nServicio: {servicio.nombre}\n"
                f"Dirección: {tenant.direccion or '📍 a confirmar con el asesor'}\n"
                f"\nSi querés cancelar, escribí: cancelar {fake_id}"
            )})

        # Mensaje genérico por defecto - manejar saludos
        if mensaje in ["hola", "hello", "hi", "buenas", "buen dia", "buenas tardes", "buenas noches"]:
            state["step"] = "welcome"
            state["is_first_contact"] = True  # Tratar saludos como primer contacto
            set_user_state(telefono, state)
            return JSONResponse(content={"mensaje": f"¡Hola! 👋 Bienvenido/a a *{tenant.comercio}*\n\n🕐 *Horarios de atención:*\nLunes a Viernes: 9:00 - 18:00\nSábados: 9:00 - 13:00\n\n📅 Para agendar una cita, escribe:\n• \"Turno\" o \"Reservar\"\n• Tu nombre completo\n• Servicio que necesitas\n• Día y horario preferido\n\n💬 Si necesitas ayuda personalizada, escribe \"Ayuda\"\n\n¿En qué podemos ayudarte hoy?"})
        
        # Manejar palabras clave básicas en cualquier momento de la conversación
        if "turno" in mensaje or "reservar" in mensaje or "agendar" in mensaje:
            servicios = tenant.servicios
            if not servicios:
                return JSONResponse(content={"mensaje": "⚠️ No hay servicios disponibles."})
            msg = "¿Qué servicio deseas reservar?\n"
            for i, s in enumerate(servicios, 1):
                msg += f"🔹{i}. {s.nombre} ({s.duracion} min, ${s.precio})\n"
            msg += "\nResponde con el número del servicio."
            state["step"] = "waiting_servicio"
            state["servicios"] = [s.id for s in servicios]
            set_user_state(telefono, state)
            return JSONResponse(content={"mensaje": msg})
        
        if "informacion" in mensaje or "info" in mensaje:
            if tenant.informacion_local:
                state["step"] = "after_info"
                set_user_state(telefono, state)
                return JSONResponse(content={"mensaje": f"{tenant.informacion_local}\n\n¿Qué deseas hacer?\n🔹 Escribe \"Turno\" para agendar\n🔹 Escribe \"Ayuda\" para hablar con un asesor"})
            else:
                return JSONResponse(content={"mensaje": "⚠️ No hay información disponible en este momento."})
        
        return JSONResponse(content={"mensaje": "❓ No entendí tu mensaje.\n\n¿Qué necesitas?\n🔹 Escribe \"Turno\" para agendar\n🔹 Escribe \"Información\" para conocer más sobre nosotros\n🔹 Escribe \"Ayuda\" para hablar con un asesor"})

    except Exception as e:
        import traceback as tb
        error_text = tb.format_exc()
        log = ErrorLog(
            cliente=tenant.comercio if 'tenant' in locals() and tenant else None,
            telefono=telefono if 'telefono' in locals() else None,
            mensaje=mensaje if 'mensaje' in locals() else None,
            error=error_text
        )
        db.add(log)
        db.commit()
        print("❌ Error general procesando mensaje:", e)
        if not state.get("error_sent"):
            state["error_sent"] = True
            set_user_state(telefono, state)
        return JSONResponse(content={"mensaje": "❌ Ocurrió un error inesperado. Por favor, intenta nuevamente más tarde."})

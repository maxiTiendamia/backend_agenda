from datetime import datetime, timezone
import pytz

def make_aware_utc(dt):
    """Convierte datetime naive a UTC aware"""
    if dt is None:
        return None
    
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def make_aware_local(dt, tz=None):
    """Convierte datetime naive a timezone local aware"""
    if dt is None:
        return None
    
    if tz is None:
        tz = pytz.timezone("America/Montevideo")
    
    if dt.tzinfo is None:
        return tz.localize(dt)
    return dt.astimezone(tz)

def utc_now():
    """Retorna fecha actual en UTC"""
    return datetime.now(timezone.utc)

def local_now(tz=None):
    """Retorna fecha actual en timezone local"""
    if tz is None:
        tz = pytz.timezone("America/Montevideo")
    return datetime.now(tz)
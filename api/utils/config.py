import os
import json

ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_USER = os.environ.get("ADMIN_USER", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "")

CALENDAR_ID = GOOGLE_CALENDAR_ID

GOOGLE_CREDENTIALS = json.loads(GOOGLE_CREDENTIALS_JSON) if GOOGLE_CREDENTIALS_JSON else {}
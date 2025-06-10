import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os

SCOPES = ['https://www.googleapis.com/auth/calendar']

credentials_info = os.environ["GOOGLE_CREDENTIALS_JSON"]
SERVICE_ACCOUNT_FILE = "/etc/secrets/calendario-zichi-d98b415d5008.json"

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)

service = build('calendar', 'v3', credentials=credentials)

def get_available_slots(calendar_id):
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    events_result = service.events().list(
        calendarId=calendar_id, timeMin=now,
        maxResults=5, singleEvents=True,
        orderBy='startTime').execute()
    events = events_result.get('items', [])

    slots = []
    for e in events:
        start = e['start'].get('dateTime') or e['start'].get('date')
        if start:
            slots.append(start)
    return slots

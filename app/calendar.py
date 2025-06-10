import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
import json

SCOPES = ['https://www.googleapis.com/auth/calendar']

credentials_info = os.environ["GOOGLE_CREDENTIALS_JSON"]
credentials_info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
credentials = service_account.Credentials.from_service_account_info(
    credentials_info, scopes=SCOPES)

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

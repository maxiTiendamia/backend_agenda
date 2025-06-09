from google.oauth2 import service_account
from googleapiclient.discovery import build
from app.config import GOOGLE_CREDENTIALS_DICT
import datetime

SCOPES = ['https://www.googleapis.com/auth/calendar']

credentials = service_account.Credentials.from_service_account_info(
    GOOGLE_CREDENTIALS_DICT, scopes=SCOPES)
service = build('calendar', 'v3', credentials=credentials)

def get_available_slots(calendar_id):
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    events_result = service.events().list(
        calendarId=calendar_id, timeMin=now,
        maxResults=5, singleEvents=True,
        orderBy='startTime').execute()
    events = events_result.get('items', [])
    return [e['start']['dateTime'] for e in events]

def create_event(calendar_id, start_datetime, user_phone):
    end_datetime = (datetime.datetime.fromisoformat(start_datetime[:-1]) + datetime.timedelta(minutes=30)).isoformat() + 'Z'
    event = {
        'summary': f'Turno reservado por {user_phone}',
        'start': {'dateTime': start_datetime, 'timeZone': 'UTC'},
        'end': {'dateTime': end_datetime, 'timeZone': 'UTC'},
    }
    event = service.events().insert(calendarId=calendar_id, body=event).execute()
    return event
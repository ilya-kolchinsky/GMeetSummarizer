import os
import re
import tempfile

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

# Define the scopes required for Google Meet and Google Drive APIs.
SCOPES = ['https://www.googleapis.com/auth/drive.readonly',
          'https://www.googleapis.com/auth/meetings.space.readonly']


def authenticate_google_api():
    """
    Handles Google API authentication using OAuth 2.0.
    """
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing existing token...")
            creds.refresh(Request())
        else:
            print("No valid token found, initiating new authentication flow...")
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds


def extract_file_id_from_drive_link(drive_link):
    """
    Extracts the Google Drive file ID from a given Google Drive sharing link.
    """
    match = re.search(r'(?:id=|/file/d/)([a-zA-Z0-9_-]+)', drive_link)
    if match:
        return match.group(1)
    return None


def list_meet_recordings(creds, message_queue=None):
    """
    Lists recent Google Meet recordings and their associated Google Drive file IDs.
    Only returns recordings that have a valid Drive File ID.
    Can write output to a message queue.
    """
    def _send_msg(msg_type, msg_content):
        if message_queue:
            message_queue.put((msg_type, msg_content))
        print(f"[Downloader] {msg_content}")

    _send_msg("info", "Fetching recent Google Meet recordings...")

    try:
        meet_service = build('meet', 'v2', credentials=creds)
        request = meet_service.conferenceRecords().list(pageSize=10)
        response = request.execute()

        recordings = []
        if 'conferenceRecords' in response:
            for record in response['conferenceRecords']:
                conference_record_id = record.get('name')
                recording_request = meet_service.conferenceRecords().recordings().list(
                    parent=conference_record_id
                )
                recording_response = recording_request.execute()

                if 'recordings' in recording_response:
                    for recording in recording_response['recordings']:
                        recording_state = recording.get('state')
                        if recording_state == 'FILE_GENERATED':
                            drive_destination = recording.get('driveDestination')
                            if drive_destination and drive_destination.get('fileId'):
                                file_id = drive_destination.get('fileId')
                                export_uri = drive_destination.get('exportUri')
                                recordings.append({
                                    'conference_record_id': conference_record_id,
                                    'recording_id': recording.get('name'),
                                    'state': recording_state,
                                    'start_time': recording.get('startTime'),
                                    'file_id': file_id,
                                    'export_uri': export_uri
                                })
        return recordings

    except HttpError as error:
        error_msg = f"[ERROR: Google Meet API] An HTTP error occurred: Status={error.resp.status}, Content={error.content.decode('utf-8')}"
        _send_msg("error", error_msg)
        return []


def download_meet_recording_to_temp_file(creds, file_id, processing_function, settings, message_queue=None):
    """
    Downloads a Google Meet recording to a temporary local file,
    then invokes the processing_function on that file, and finally deletes the temp file.
    """
    def _send_msg(msg_type, msg_content):
        if message_queue:
            message_queue.put((msg_type, msg_content))
        print(f"[Downloader] {msg_content}")

    try:
        drive_service = build('drive', 'v3', credentials=creds)

        _send_msg("info", f"Attempting to fetch file `{file_id}` for download.")

        request = drive_service.files().get_media(fileId=file_id)

        # Create a temporary file to store the downloaded video
        # The 'delete=False' allows us to get the path before the file is closed and deleted by the context manager.
        # We will manually delete it later.
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_file:
            temp_file_path = temp_file.name

            _send_msg("info", f"Downloading to temporary file: `{temp_file_path}`")

            downloader = MediaIoBaseDownload(temp_file, request)
            done = False

            # Create a progress bar in Streamlit if message_queue is available
            progress_bar = None
            status_text = None
            if message_queue:
                # We use a placeholder for the progress bar to update it.
                # This placeholder is within the context of the main Streamlit app.
                message_queue.put(("progress_init", "Download in progress..."))  # Signal to init progress bar

            while done is False:
                status, done = downloader.next_chunk()
                percent = int(status.progress() * 100)
                if message_queue:
                    # Send progress updates to the queue
                    message_queue.put(("progress_update", percent))
                print(f"[Downloader] Download progress: {percent}%", end='\r')

            _send_msg("success", f"Download complete to `{temp_file_path}`")
            if message_queue:
                message_queue.put(("progress_complete", None))  # Signal completion of progress bar

        # Now, invoke the processing function on the temporary file
        processing_function(temp_file_path, settings, message_queue)

    except HttpError as error:
        error_msg = f"[ERROR: Google Drive API] An HTTP error occurred during download: Status={error.resp.status}, Content={error.content.decode('utf-8')}"
        _send_msg("error", error_msg)
        if message_queue:
            message_queue.put(("progress_complete", None))  # Ensure progress bar is removed/completed on error
    except Exception as e:
        error_msg = f"[ERROR] An unexpected error occurred: {e}"
        _send_msg("error", error_msg)
        if message_queue:
            message_queue.put(("progress_complete", None))  # Ensure progress bar is removed/completed on error
    finally:
        # Ensure the temporary file is deleted
        if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                _send_msg("info", f"Deleted temporary file: `{temp_file_path}`")
            except OSError as e:
                error_msg = f"[Cleanup] Error deleting temporary file `{temp_file_path}`: {e}"
                _send_msg("error", error_msg)

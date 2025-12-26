import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
SERVICE_ACCOUNT_FILE = 'credentials.json' # Ye file Google Cloud se download krni paregi

class DriveManager:
    def __init__(self):
        self.service = None
        if os.path.exists(SERVICE_ACCOUNT_FILE):
            try:
                creds = service_account.Credentials.from_service_account_file(
                    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
                self.service = build('drive', 'v3', credentials=creds)
                print("✅ Google Drive Connected.")
            except Exception as e:
                print(f"⚠️ Drive Auth Error: {e}")
        else:
            print("⚠️ 'credentials.json' not found. Drive features disabled.")

    def get_client_files(self, client_email):
        """
        Client ki email se uska folder dhoondta hai aur files list karta hai.
        Assumption: Folder ka naam client ka email ya naam hai.
        """
        if not self.service: return []

        try:
            # 1. Search for Folder with Client's Email/Name
            query = f"mimeType = 'application/vnd.google-apps.folder' and name contains '{client_email}' and trashed = false"
            results = self.service.files().list(q=query, fields="files(id, name)").execute()
            folders = results.get('files', [])

            if not folders:
                return [] # Folder nahi mila

            folder_id = folders[0]['id']

            # 2. List Files inside that Folder
            file_query = f"'{folder_id}' in parents and trashed = false"
            file_results = self.service.files().list(
                q=file_query, 
                fields="files(id, name, webViewLink, thumbnailLink)"
            ).execute()
            
            return file_results.get('files', [])

        except Exception as e:
            print(f"❌ Drive Fetch Error: {e}")
            return []
import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

# Aapko Wix se ek URL milega (Setup step mein bataunga)
# Example: https://www.fandldesignbuilders.com/_functions/add_subscriber
WIX_WEBHOOK_URL = os.getenv("WIX_WEBHOOK_URL") 

class WixManager:
    def __init__(self):
        if not WIX_WEBHOOK_URL:
            print("⚠️ Wix Webhook URL not found via .env! Newsletter sync will be skipped.")
            self.active = False
        else:
            self.active = True

    def add_contact_to_wix(self, name, email, phone):
        """
        Sends Lead Data to Wix Contacts & Newsletter.
        """
        if not self.active:
            return "Skipped (No URL)"

        print(f"📨 Syncing Lead to Wix: {email}...")
        
        try:
            # Splitting Name
            name_parts = name.strip().split(" ")
            first_name = name_parts[0]
            last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

            payload = {
                "firstName": first_name,
                "lastName": last_name,
                "email": email,
                "phone": phone,
                "source": "LOFTY AI Chatbot"
            }

            # Sending Request to Wix Webhook
            response = requests.post(
                WIX_WEBHOOK_URL, 
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )

            if response.status_code == 200 or response.status_code == 201:
                print("✅ Successfully added to Wix Newsletter!")
                return True
            else:
                print(f"⚠️ Wix Sync Failed: {response.text}")
                return False

        except Exception as e:
            print(f"❌ Wix Error: {e}")
            return False
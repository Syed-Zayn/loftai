import os
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from dotenv import load_dotenv

# Force reload of .env file
load_dotenv(override=True)

class TwilioManager:
    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        
        # --- CRITICAL FIX: Strip spaces just in case ---
        phone = os.getenv("TWILIO_PHONE_NUMBER")
        self.phone_number = phone.strip() if phone else None
        
        self.forward_to_number = os.getenv("CLIENT_PERSONAL_PHONE")

        # 🔍 DEBUG PRINT (Terminal me check krna k ye Number print ho rha hai ya None)
        print(f"🔧 Twilio Config Loaded: SID={self.account_sid[:5]}... | FROM NUMBER={self.phone_number}")

        if self.account_sid and self.auth_token:
            self.client = Client(self.account_sid, self.auth_token)
        else:
            self.client = None
            print("⚠️ Twilio Credentials Missing!")

    def send_sms(self, to_number, body):
        """AI ka reply user ko SMS ke zariye bheje ga."""
        if not self.client: 
            print("❌ Twilio Client not initialized.")
            return False
            
        if not self.phone_number:
            print("❌ ERROR: 'TWILIO_PHONE_NUMBER' is Missing in .env file!")
            return False

        try:
            print(f"📤 Attempting to send SMS from {self.phone_number} to {to_number}")
            message = self.client.messages.create(
                body=body,
                from_=self.phone_number, # <--- Ye parameter missing tha
                to=to_number
            )
            print(f"✅ SMS Sent to {to_number}: {body}")
            return message.sid
        except Exception as e:
            print(f"❌ SMS Error: {e}")
            return None

    def handle_incoming_call(self):
        """Jab koi call karega to ye 'Voice Response' generate karega."""
        resp = VoiceResponse()
        
        # 1. Professional Greeting (Lofty Voice)
        resp.say("Welcome to F and L Design Builders. Please hold while we connect you to a project manager.", voice='alice', language='en-US')
        
        # 2. Forward Call to Client (Lorena/Partner)
        if self.forward_to_number:
            resp.dial(self.forward_to_number)
        else:
            resp.say("No agent is currently available. Please leave a message.")
        
        return str(resp)
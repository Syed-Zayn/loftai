import os
import uvicorn
import httpx
import logging
import textwrap  # <--- NEW IMPORT FOR CHUNKING
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Form, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv
from fastapi import Response # For TwiML XML response
from twilio_client import TwilioManager
from drive_client import DriveManager
from fastapi.staticfiles import StaticFiles
# --- CUSTOM MODULES IMPORTS ---
# Make sure these files exist in the same folder
from agent_graph import get_app
from hubspot_client import HubSpotManager

# --- CONFIGURATION & LOGGING ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LOFTY_API")


# Initialize External Managers
hubspot_manager = HubSpotManager()
# Initialize Managers
twilio_manager = TwilioManager()
drive_manager = DriveManager()
# Meta/Instagram Config (From .env)
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "secure_token_123") 
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
IG_USER_ID = os.getenv("INSTAGRAM_BUSINESS_ID")


# --- 1. GLOBAL STATE (For AI Brain Persistence) ---
app_state = {}

# --- 2. LIFESPAN MANAGER (Async Startup) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Booting up LOFTY API (Unified Production System)...")
    try:
        # Initialize LangGraph Agent (Connects to Neon DB Async Pool)
        app_state["agent"] = await get_app()
        print("✅ LOFTY Agent Loaded & Connected to DB.")
    except Exception as e:
        print(f"❌ Critical Error Loading Agent: {e}")
    yield
    print("🛑 Shutting down server...")

# --- 3. FASTAPI APP SETUP ---
app = FastAPI(title="F&L Design Builders - Unified Backend", version="3.0", lifespan=lifespan)

if not os.path.exists("generated_quotes"):
    os.makedirs("generated_quotes")
app.mount("/quotes", StaticFiles(directory="generated_quotes"), name="quotes")

# CORS (Allow Wix & Frontend access)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 4. DATA MODELS ---

class QuickReply(BaseModel):
    label: str 
    value: str

class ChatRequest(BaseModel):
    message: str
    session_id: str 
    platform: str = "web"
    quick_replies: List[QuickReply] = []

class ChatResponse(BaseModel):
    response: str
    actions: list = [] 

class PortalRequest(BaseModel):
    email: str

class PortalLoginRequest(BaseModel):
    email: str




# --- STANDARD BUTTONS LIST ---
DEFAULT_BUTTONS = [
    QuickReply(label="📅 Book a Consultation", value="I want to schedule a consultation"),
    QuickReply(label="💬 Talk to Specialist", value="I need to speak to a project manager"),
    QuickReply(label="📐 Get a Design Quote", value="I want a renovation quote"),
    QuickReply(label="🏡 Start My Project", value="I want to start a new project")
]

# ============================================================
#  SECTION A: INSTAGRAM AUTOMATION (Async & Background)
#  CRITICAL FIX APPLIED: Using graph.instagram.com + Message Chunking
# ============================================================

# 1. HELPER: Send Message via Meta API (Async)
async def send_meta_reply_http(recipient_id: str, text: str, type: str):
    """
    Sends the final text back to Instagram User via Graph API.
    UPDATED: Now splits long messages into chunks to avoid 1000 char limit error.
    """
    if not PAGE_ACCESS_TOKEN:
        logger.error("⚠️ Page Access Token Missing! Cannot reply.")
        return

    # --- MESSAGE SPLITTER LOGIC ---
    # Break message into 950 char chunks (Leaving 50 chars buffer)
    chunks = textwrap.wrap(text, width=950, replace_whitespace=False, drop_whitespace=False)

    async with httpx.AsyncClient() as client:
        # UPDATED: Using graph.instagram.com based on official docs for User Tokens
        base_url = "https://graph.instagram.com/v21.0"
        
        for i, chunk in enumerate(chunks):
            url = ""
            payload = {}
            
            try:
                if type == "dm":
                    # Doc: POST /<IG_ID>/messages
                    url = f"{base_url}/{IG_USER_ID}/messages?access_token={PAGE_ACCESS_TOKEN}"
                    payload = {
                        "recipient": {"id": recipient_id},
                        "message": {"text": chunk}
                    }
                
                elif type == "comment":
                    # Comments usually work via: /<COMMENT_ID>/replies
                    url = f"{base_url}/{recipient_id}/replies?access_token={PAGE_ACCESS_TOKEN}"
                    payload = {"message": chunk}
                    
                print(f"📤 Sending Reply Chunk {i+1}/{len(chunks)} to Meta ({len(chunk)} chars)...") 
                
                response = await client.post(url, json=payload, timeout=10.0)
                
                if response.status_code == 200:
                    logger.info(f"✅ Meta Reply Chunk {i+1} Sent to {recipient_id}")
                else:
                    logger.error(f"❌ Meta API Error on Chunk {i+1}: {response.text}")

            except Exception as e:
                logger.error(f"⚠️ Network Error sending to Meta: {e}")

# 2. HELPER: Process Logic (The Brain) - Runs in Background
async def process_instagram_event(target_id: str, user_text: str, type: str):
    """
    This runs in the BACKGROUND. It calls the AI Agent and then sends the reply.
    """
    logger.info(f"🧠 Processing {type} from {target_id}...")
    
    try:
        # Context Injection for the AI (To guide the persona)
        context_prefix = ""
        if type == "comment":
            context_prefix = "[CONTEXT: User commented on an Instagram Post. Keep reply public, short, engaging, and luxury tone.] "
        elif type == "dm":
            context_prefix = "[CONTEXT: User sent a Direct Message. Be helpful, warm, act as a concierge.] "

        final_msg = context_prefix + user_text
        
        # Call LangGraph Agent
        agent = app_state.get("agent")
        ai_reply = "Thank you for connecting with F&L Design Builders. We will be with you shortly." # Default fallback

        if agent:
            # Unique Thread ID for Instagram Users (Persistent Memory)
            config = {"configurable": {"thread_id": f"ig_{target_id}"}}
            
            response_text = ""
            async for event in agent.astream({"messages": [HumanMessage(content=final_msg)]}, config=config):
                if "agent" in event:
                    raw_content = event["agent"]["messages"][-1].content
                    # Clean Response logic (Handling List vs String)
                    if isinstance(raw_content, list):
                        parts = [item.get("text", "") if isinstance(item, dict) else str(item) for item in raw_content]
                        response_text = "".join(parts)
                    else:
                        response_text = str(raw_content)
            
            if response_text:
                ai_reply = response_text

        # Send Reply via Meta API (Now handles chunks)
        await send_meta_reply_http(target_id, ai_reply, type)

    except Exception as e:
        logger.error(f"⚠️ AI Processing Error: {e}")

# 3. WEBHOOK VERIFICATION (Meta Challenge)
@app.get("/webhook")
async def verify_webhook(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge")
):
    """
    Meta sends a GET request to verify we own the server.
    """
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("✅ Meta Webhook Verified Successfully!")
        return PlainTextResponse(content=challenge, status_code=200)
    raise HTTPException(status_code=403, detail="Verification Failed")

# 4. WEBHOOK LISTENER (The Entry Point)
@app.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives events from Meta.
    UPDATED: Includes Debug Prints & Standby Support.
    """
    try:
        # 1. Get Raw Data
        payload = await request.json()
        
        # 🔍 JASOOSI LOG
        print("\n" + "="*50)
        print(f"📨 INCOMING PAYLOAD: {payload}")
        print("="*50 + "\n")

        for entry in payload.get("entry", []):
            
            # --- A. Handle PRIMARY DMs (Messaging) ---
            if "messaging" in entry:
                print("🔹 Event Type: Messaging (Primary)")
                for event in entry["messaging"]:
                    sender_id = event.get("sender", {}).get("id")
                    message = event.get("message", {})
                    text = message.get("text")
                    
                    if message.get("is_echo"):
                        print("ℹ️ Detected Echo (Bot's own message). Skipping.")
                        continue

                    if text and sender_id:
                        print(f"✅ MESSAGE RECEIVED from {sender_id}: {text}")
                        # Action: Process in Background
                        background_tasks.add_task(process_instagram_event, sender_id, text, "dm")
                    else:
                        print("⚠️ Messaging event received, but no text found.")

            # --- B. Handle STANDBY DMs ---
            elif "standby" in entry:
                print("🟠 Event Type: STANDBY (Message Requests)")
                for event in entry["standby"]:
                    sender_id = event.get("sender", {}).get("id")
                    message = event.get("message", {})
                    text = message.get("text")

                    if text and sender_id and not message.get("is_echo"):
                        print(f"✅ STANDBY MESSAGE processed from {sender_id}: {text}")
                        background_tasks.add_task(process_instagram_event, sender_id, text, "dm")

            # --- C. Handle COMMENTS ---
            elif "changes" in entry:
                print("🔹 Event Type: Changes (Comment/Post)")
                for change in entry["changes"]:
                    if change.get("field") == "comments":
                        value = change.get("value", {})
                        comment_id = value.get("id")
                        text = value.get("text")
                        user_id = value.get("from", {}).get("id")
                        
                        if user_id == IG_USER_ID: 
                            continue 
                        
                        if text:
                            print(f"💬 COMMENT RECEIVED from {user_id}: {text}")
                            background_tasks.add_task(process_instagram_event, comment_id, text, "comment")

        return {"status": "ok"}

    except Exception as e:
        print(f"❌ Webhook CRASH: {e}")
        logger.error(f"❌ Webhook Parse Error: {e}")
        return {"status": "error", "message": str(e)}

# ============================================================
#  SECTION B: CLIENT PORTAL & QUOTES
# ============================================================

@app.post("/portal/get-data")
async def get_portal_data(request: PortalLoginRequest):
    print(f"🔍 Checking Portal for: {request.email}")
    
    # 1. HubSpot se Project Details lo (Existing Code)
    deal_data = hubspot_manager.get_deal_by_email(request.email)
    
    # 2. Google Drive se Files lo (NEW CODE)
    project_files = drive_manager.get_client_files(request.email)
    
    if not deal_data:
         # Agar deal nahi mili, tab bhi files check karo shayad purani hon
         return {
             "found": True if project_files else False,
             "client_name": "Valued Client",
             "project_name": "No Active Deal Found",
             "status": "Contact Admin",
             "files": project_files # Ab files ki list jayegi
         }
    
    return {
        "found": True,
        "client_name": deal_data.get('firstname', 'Valued Client'),
        "project_name": deal_data.get('project', 'Renovation Project'),
        "status": deal_data.get('status', 'In Progress'),
        "amount": deal_data.get('amount', '0'),
        "files": project_files # Frontend is list ko gallery bana dega
    }





# --- TWILIO WEBHOOKS ---

@app.post("/twilio/voice")
async def handle_voice_call(request: Request):
    """Twilio yahan call bhejega jab koi number dial karega."""
    print("📞 Incoming Voice Call...")
    xml_response = twilio_manager.handle_incoming_call()
    return Response(content=xml_response, media_type="application/xml")

@app.post("/twilio/sms")
async def handle_sms(request: Request):
    """Twilio yahan SMS bhejega."""
    form_data = await request.form()
    sender_number = form_data.get("From")
    message_body = form_data.get("Body")
    
    print(f"📩 SMS from {sender_number}: {message_body}")
    
    # Send to AI Agent (Lofty)
    agent = app_state.get("agent")
    if agent:
        config = {"configurable": {"thread_id": f"sms_{sender_number}"}}
        response_text = "Checking..."
        
        async for event in agent.astream({"messages": [HumanMessage(content=message_body)]}, config=config):
             if "agent" in event:
                # --- FIX: Extract clean text from LangChain response ---
                raw_content = event["agent"]["messages"][-1].content
                
                # Agar response list hai (JSON), to usay text mein convert karo
                if isinstance(raw_content, list):
                    response_text = " ".join([item.get("text", "") for item in raw_content if isinstance(item, dict)])
                else:
                    response_text = str(raw_content)
        
        # Reply via Twilio (Clean Text)
        twilio_manager.send_sms(sender_number, response_text)
        
    return "OK"

@app.get("/quote/accept", response_class=HTMLResponse)
async def accept_quote(deal_id: str):
    """HubSpot Update + Success Page."""
    print(f"🎉 Quote Accepted: {deal_id}")
    hubspot_manager.update_deal_stage(deal_id, "closedwon")
    
    return """
    <html>
        <head>
            <title>Quote Accepted - F&L Design Builders</title>
            <style>
                body { font-family: 'Helvetica', sans-serif; background: #f8f9fa; text-align: center; padding: 50px; }
                .box { background: white; padding: 40px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-width: 500px; margin: auto; }
                h1 { color: #d4af37; }
                .btn { display: inline-block; background: black; color: #d4af37; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold; margin-top: 20px;}
            </style>
        </head>
        <body>
            <div class="box">
                <h1>Thank You!</h1>
                <p>We are thrilled to begin this journey with you.</p>
                <p>Your acceptance has been confirmed. Our team will contact you shortly.</p>
                <a href="https://www.fandldesignbuilders.com" class="btn">Return to Website</a>
            </div>
        </body>
    </html>
    """

@app.get("/quote/reject", response_class=HTMLResponse)
async def reject_quote_form(deal_id: str):
    """Feedback Form."""
    return f"""
    <html>
        <head>
            <title>Quote Feedback</title>
            <style>
                body {{ font-family: 'Helvetica', sans-serif; background: #f8f9fa; text-align: center; padding: 50px; }}
                .box {{ background: white; padding: 40px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-width: 500px; margin: auto; }}
                textarea {{ width: 100%; height: 100px; margin: 15px 0; padding: 10px; border: 1px solid #ccc; border-radius: 4px; }}
                .btn {{ background: #dc3545; color: white; padding: 12px 24px; border: none; border-radius: 4px; cursor: pointer; }}
            </style>
        </head>
        <body>
            <div class="box">
                <h2>We Value Your Feedback</h2>
                <p>Please let us know why this quote didn't work for you.</p>
                <form action="/quote/reject/submit" method="post">
                    <input type="hidden" name="deal_id" value="{deal_id}">
                    <textarea name="reason" placeholder="Budget, Timing, Competitor..." required></textarea>
                    <button type="submit" class="btn">Submit Feedback</button>
                </form>
            </div>
        </body>
    </html>
    """

@app.post("/quote/reject/submit", response_class=HTMLResponse)
async def reject_quote_submit(deal_id: str = Form(...), reason: str = Form(...)):
    print(f"📉 Quote Rejected: {deal_id} Reason: {reason}")
    hubspot_manager.update_deal_stage(deal_id, "closedlost")
    hubspot_manager.add_note_to_deal(deal_id, f"REJECTED: {reason}")
    return "<html><body style='text-align:center; padding:50px; font-family:Helvetica;'><h3>Thank you. Your feedback has been recorded.</h3></body></html>"

# ============================================================
#  SECTION C: WEBSITE CHATBOT (Wix)
# ============================================================




@app.post("/capture-lead")
async def capture_lead(data: dict):
    """Wix frontend se lead capture karne ke liye."""
    print(f"📥 New Lead from Website: {data}")
    # Aap yahan HubSpot manager use karke contact create kar sakte hain
    try:
        hubspot_manager.create_or_update_contact(email=data.get("email"), firstname=data.get("name"), phone=data.get("phone"))
        return {"status": "success", "message": "Lead captured in HubSpot"}
    except Exception as e:
        logger.error(f"❌ HubSpot Lead Sync Error: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """
    Main Chat Endpoint for Wix Website.
    """
    try:
        agent = app_state.get("agent")
        if not agent:
            raise HTTPException(status_code=503, detail="AI Agent is still loading... Please retry in 5s.")

        user_msg = request.message
        
        if request.platform == "bot_script":
             print(f"🤖 Automated Script Query: {user_msg}")
             user_msg = f"[Context: Reply short for Instagram Comment]: {user_msg}"

        config = {"configurable": {"thread_id": request.session_id}}
        
        final_response = ""
        tool_executed = False
        
        async for event in agent.astream(
            {"messages": [HumanMessage(content=user_msg)]},
            config=config
        ):
            if "agent" in event:
                raw_content = event["agent"]["messages"][-1].content
                if isinstance(raw_content, list):
                    parts = []
                    for item in raw_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(item.get("text", ""))
                        elif isinstance(item, str):
                            parts.append(item)
                    final_response = "".join(parts)
                else:
                    final_response = str(raw_content)
            
            if "tools" in event:
                tool_executed = True

        if not final_response:
            final_response = "Checking design records... One moment."

        return ChatResponse(
            response=final_response,
            actions=["lead_captured"] if tool_executed else [],
            quick_replies=DEFAULT_BUTTONS
        )

    except Exception as e:
        print(f"⚠️ Chat Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def health_check():
    return {"status": "active", "system": "F&L Unified Backend", "version": "3.0", "concurrency": "enabled"}

if __name__ == "__main__":

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)


import os
import asyncio
from typing import Annotated, Literal, TypedDict
from dotenv import load_dotenv

# LangChain Imports
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from langgraph.graph.message import add_messages 

# --- CUSTOM MODULES (Integration) ---
from wix_client import WixManager
from hubspot_client import HubSpotManager
from quote_generator import QuoteGenerator
from twilio_client import TwilioManager 
from drive_client import DriveManager

# 1. Environment & Setup
load_dotenv()
wix = WixManager()
hubspot = HubSpotManager()
pdf_engine = QuoteGenerator()
twilio = TwilioManager()
drive = DriveManager()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

# 2. Setup Pinecone (Brain)
print("🧠 Initializing Luxury AI Memory...")
embeddings = GoogleGenerativeAIEmbeddings(
    model="gemini-embedding-001",
    google_api_key=GOOGLE_API_KEY,
    task_type="retrieval_document"
)

vectorstore = PineconeVectorStore(
    index_name="fl-builders-index",
    embedding=embeddings,
    pinecone_api_key=PINECONE_API_KEY
)

# --- 3. HIGH-LEVEL TOOLS (The "Concierge" Suite) ---
from langchain_core.tools import tool

@tool
def save_lead_to_hubspot(name: str, email: str, phone: str):
    """
    Saves a new lead to HubSpot CRM AND Wix Newsletter.
    Triggers an internal SMS alert to Felicity/Lorena via Twilio.
    """
    status_msg = []
    
    # A. Save to CRM
    contact_id = hubspot.create_lead(name, email, phone)
    status_msg.append(f"CRM ID: {contact_id}")

    # B. Sync to Wix Marketing
    wix_success = wix.add_contact_to_wix(name, email, phone)
    if wix_success: status_msg.append("Wix Sync OK")

    # C. "Call Center" Alert (High-Level Feature)
    if twilio.client:
        alert_body = f"🚀 NEW LEAD: {name} ({phone}). Check HubSpot now."
        admin_phone = os.getenv("CLIENT_PERSONAL_PHONE") 
        if admin_phone:
            twilio.send_sms(admin_phone, alert_body)
            status_msg.append("SMS Alert Sent")

    return f"Lead Securely Stored: {', '.join(status_msg)}."

@tool
def generate_quote_and_deal(project_type: str, budget: str, user_name: str, email: str, phone: str):
    """
    Generates a PDF Quote + HubSpot Deal.
    Use this when user wants a formal estimate.
    """
    # 1. Ensure Lead Exists
    contact_id = hubspot.create_lead(user_name, email, phone)
    
    # 2. Create Deal
    deal_id = hubspot.create_deal_with_quote(contact_id, project_type, budget, "Generating...")
    
    if "Error" in str(deal_id):
        return f"System Error: Could not initialize deal ({deal_id})."

    # 3. Generate Luxury PDF
    try:
        result = pdf_engine.generate_pdf(user_name, project_type, budget, deal_id)
        filename = os.path.basename(result[1]) if isinstance(result, tuple) else os.path.basename(result)
        
        base_url = os.getenv("API_BASE_URL", "http://localhost:8000") 
        pdf_link = f"{base_url}/quotes/{filename}"
        
        return f"Quote Generated Successfully. Download Link: {pdf_link}"
    except Exception as e:
        return f"PDF Generation Error: {str(e)}"

@tool
def check_financing_eligibility(budget_concern: str):
    """
    Checks financing options. Use if user mentions 'budget', 'cost', 'expensive'.
    """
    return "Eligible for: F&L Exclusive 8-Months Same-As-Cash Financing Program. (Approvals in 60 seconds)."

@tool
def get_secure_upload_link():
    """
    Provides a secure link for users to upload photos/videos of their space.
    """
    return "Secure Upload Portal: https://forms.google.com/f-and-l-secure-upload"

# --- NEW HIGH-LEVEL TOOLS ---

@tool
def check_project_status(email: str):
    """
    [CLIENT LOGIN FEATURE]
    Checks the status of an active renovation project.
    Returns the current Stage (e.g., 'Demolition', 'Finishing') and Google Drive Folder Link.
    Use when user asks: "How is my project going?", "Updates?", "Login".
    """
    deal_info = hubspot.get_deal_by_email(email)
    
    if not deal_info:
        return "No active project found for this email. Please check with your Project Manager."
    
    files = drive.get_client_files(email)
    file_count = len(files)
    
    return f"""
    PROJECT STATUS: {deal_info.get('status', 'In Progress')}
    CURRENT PHASE: {deal_info.get('project', 'General Renovation')}
    DOCUMENTS FOUND: {file_count} files available.
    
    ACCESS PORTAL: {deal_info.get('link', 'https://drive.google.com')}
    """

@tool
def request_immediate_callback(phone: str, query: str):
    """
    [CALL CENTER FEATURE]
    Triggers an emergency/immediate callback request to the Project Manager via Twilio.
    Use when user is frustrated or asks to 'speak to a human'.
    """
    admin_phone = os.getenv("CLIENT_PERSONAL_PHONE")
    if admin_phone and twilio.client:
        twilio.send_sms(admin_phone, f"⚠️ CALLBACK REQUEST: {phone}. Query: {query}")
        return "Priority Callback Requested. A Senior Project Manager will call you within 15 minutes."
    return "Request logged. Our team will contact you shortly."

# REGISTER ALL 6 TOOLS (Critical for full functionality)
tools = [
    save_lead_to_hubspot, 
    generate_quote_and_deal, 
    check_financing_eligibility, 
    get_secure_upload_link,
    check_project_status,      
    request_immediate_callback 
]
tool_node = ToolNode(tools)

# 4. Initialize Model (Zero Temperature for Strictness)
model = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=GOOGLE_API_KEY,
    temperature=0.0 
).bind_tools(tools)

# --- 5. INTELLIGENT STATE LOGIC ---

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    intent: str # 'start', 'pricing', 'design', 'general', etc.
    context: str

# NODE 1: Smart Classification (Updated for 8-Flow Logic)
def classify_intent_node(state: AgentState):
    """
    Decides which FLOW (1-8) the user is currently in based on Client Requirements.
    """
    last_msg = state["messages"][-1].content.lower()
    
    # FLOW 8: Human Handoff
    if any(x in last_msg for x in ["human", "agent", "call me", "talk to someone", "callback"]):
        return {"intent": "handoff"}
    
    # FLOW 2: Start Project / Residential / Commercial
    if any(x in last_msg for x in ["start", "build", "renovate", "new project", "addition", "remodel", "commercial", "residential"]):
        return {"intent": "start_project"}

    # FLOW 3: Design
    if any(x in last_msg for x in ["design", "architect", "plans", "drawing", "blueprints"]):
        return {"intent": "design"}

    # FLOW 4: Pricing / Budget
    if any(x in last_msg for x in ["price", "cost", "budget", "quote", "estimate", "fees", "expensive"]):
        return {"intent": "pricing"}

    # FLOW 5: Timeline
    if any(x in last_msg for x in ["how long", "timeline", "schedule", "process", "updates", "time"]):
        return {"intent": "timeline"}

    # FLOW 6: Permits / Trust
    if any(x in last_msg for x in ["permit", "license", "insured", "warranty", "inspection", "insurance"]):
        return {"intent": "permits"}

    # FLOW 7: Why Us / Trust
    if any(x in last_msg for x in ["why you", "compare", "trust", "best", "portfolio", "reviews"]):
        return {"intent": "why_us"}

    # Login / Portal Check
    if any(x in last_msg for x in ["login", "status", "portal", "files"]):
        return {"intent": "login"}
        
    # Flow 2b: Follow up on Plans
    if "plans" in last_msg or "design help" in last_msg:
        return {"intent": "start_project_followup"}

    # Default / General Query
    return {"intent": "general"}

# NODE 2: Contextual Retrieval
def retrieve_node(state: AgentState):
    last_msg = state["messages"][-1].content
    # Retrieve RAG context from Pinecone
    docs = vectorstore.similarity_search(last_msg, k=2) 
    context_text = "\n".join([d.page_content for d in docs])
    return {"context": context_text}

# NODE 3: The Brain (Generation - Updated for FABDL 8 Flows)
def generate_node(state: AgentState):
    intent = state.get("intent", "general")
    context = state.get("context", "")
    messages = state["messages"]
    
    # A. Sanitizer
    clean_messages = []
    for m in messages:
        if isinstance(m, AIMessage) and not m.content and m.tool_calls:
            m.content = "Consulting records..." 
        clean_messages.append(m)

    # --- DYNAMIC SYSTEM PROMPTS BASED ON FLOW ---
    
    base_instruction = """
    You are 'LOFTY', the AI Assistant for F&L Design Builders (Fabdl).
    Your tone is Professional, Welcoming, and Efficient.
    Do NOT use large paragraphs. Use Bullet points.
    Your location: Washington DC, Maryland & Virginia (DMV).
    """

    if intent == "start_project":
        prompt = f"""{base_instruction}
        **FLOW: Start a New Project**
        User wants to build or renovate.
        1. Acknowledge excitement.
        2. Ask EXACTLY: "Great! What type of project are you planning? (Residential, Commercial, Renovation, or Addition?)"
        """
        
    elif intent == "start_project_followup":
        prompt = f"""{base_instruction}
        **FLOW: Plans Check**
        Ask EXACTLY: "Do you already have plans, or do you need design help?"
        """

    elif intent == "design":
        prompt = f"""{base_instruction}
        **FLOW: Design Services**
        Explain we are a full Design-Build firm.
        - Yes, design is included in our process.
        - Yes, we work with external architects too.
        - Yes, you are involved in every step.
        End with: "Ready to start your Design Consultation?"
        """

    elif intent == "pricing":
        prompt = f"""{base_instruction}
        **FLOW: Pricing & Budget**
        Explain pricing depends on scope/materials.
        - We offer value-engineering to stay in budget.
        - No hidden fees. All costs discussed upfront.
        - Mention: We offer **8-Months Same-As-Cash Financing**.
        - Ask EXACTLY: "Do you have an estimated budget range?"
        """

    elif intent == "timeline":
        prompt = f"""{base_instruction}
        **FLOW: Timeline & Process**
        Outline the 5 Steps:
        1. Consultation & Vision
        2. Design & Planning
        3. Budget Approval
        4. Construction
        5. Final Walkthrough
        Say: "We provide a clear timeline after planning."
        """

    elif intent == "permits":
        prompt = f"""{base_instruction}
        **FLOW: Permits & Licensing**
        Confirm:
        - Yes, we handle ALL permits and approvals.
        - Yes, we are Fully Licensed & Insured.
        - Yes, we offer Warranties on our work.
        """

    elif intent == "why_us":
        prompt = f"""{base_instruction}
        **FLOW: Why Choose Fabdl?**
        Highlight:
        - One-team design & build
        - Transparent pricing
        - High-quality craftsmanship
        - Stress-free project management
        - Exclusive partnership with **Venicasa** (Luxury Furniture).
        """

    elif intent == "handoff":
        prompt = f"""{base_instruction}
        **FLOW: Human Handoff**
        Say: "I'll connect you with a project specialist immediately."
        Use tool 'request_immediate_callback' if they provide a number.
        """
        
    elif intent == "login":
        prompt = f"""{base_instruction}
        **FLOW: Client Portal**
        User wants to check status.
        Use the 'check_project_status' tool using their email.
        """

    else:
        # Greeting / General RAG
        prompt = f"""{base_instruction}
        **FLOW: General / Greeting**
        If greeting: "👋 Hi! Welcome to F&L Design Builders. How can I help you today?"
        If specific question, use this context: {context}
        Keep it short.
        """

    final_input = [SystemMessage(content=prompt)] + clean_messages
    response = model.invoke(final_input)
    return {"messages": [response]}

def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    if state["messages"][-1].tool_calls:
        return "tools"
    return "__end__"

# --- 6. GRAPH CONSTRUCTION ---
workflow = StateGraph(AgentState)
workflow.add_node("classify", classify_intent_node)
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("agent", generate_node)
workflow.add_node("tools", tool_node)

workflow.add_edge(START, "classify")
workflow.add_edge("classify", "retrieve")
workflow.add_edge("retrieve", "agent")
workflow.add_conditional_edges("agent", should_continue)
workflow.add_edge("tools", "agent")

# --- 7. PRODUCTION COMPILATION ---
async def get_app():
    db_url = os.getenv("NEON_DB_URL")
    
    # Connection Arguments (Optimized for Production to prevent Crashes)
    connection_kwargs = {
        "autocommit": True, 
        "prepare_threshold": 0,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5
    }
    
    # Async Pool (Resilient to disconnects)
    async_pool = AsyncConnectionPool(
        conninfo=db_url, 
        max_size=20, 
        kwargs=connection_kwargs, 
        open=False,
        # --- STABILITY SETTINGS ---
        min_size=1,          # Keep 1 connection alive
        max_lifetime=120,    # Refresh connection every 2 mins
        check=AsyncConnectionPool.check_connection, 
        timeout=10           
    )
    
    await async_pool.open()
    checkpointer = AsyncPostgresSaver(async_pool)
    await checkpointer.setup() 
    app = workflow.compile(checkpointer=checkpointer)

    return app

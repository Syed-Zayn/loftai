import os
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

# REGISTER ALL 6 TOOLS
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
    user_role: str # 'homeowner' or 'realtor'
    context: str

# NODE 1: Smart Classification
def classify_user_node(state: AgentState):
    if state.get("user_role"): return {"user_role": state["user_role"]}
    
    last_msg = state["messages"][-1].content.lower()
    
    # Expanded Keyword List
    realtor_keywords = [
        "selling", "listing", "client", "market", "roi", "investor", "flip", 
        "broker", "agent", "commission", "pre-listing", "market value", "closing"
    ]
    
    if any(x in last_msg for x in realtor_keywords):
        return {"user_role": "realtor"}
    return {"user_role": "homeowner"}

# NODE 2: Contextual Retrieval
def retrieve_node(state: AgentState):
    last_msg = state["messages"][-1].content
    role = state.get("user_role", "homeowner")
    
    if role == "realtor":
        query = f"{last_msg} realtor services commission ROI pre-listing partnership"
    else:
        query = f"{last_msg} luxury renovation design style timeline financing process paint of hope"
        
    # k=6 is optimal. Higher (30) creates noise and hallucinations.
    docs = vectorstore.similarity_search(query, k=35)
    context_text = "\n\n".join([f"[Source: {d.metadata.get('source', 'doc')}] {d.page_content}" for d in docs])
    return {"context": context_text}

# NODE 3: The Brain (Generation)
def generate_node(state: AgentState):
    context = state.get("context", "")
    role = state.get("user_role", "homeowner")
    messages = state["messages"]
    
    # A. Sanitizer
    clean_messages = []
    for m in messages:
        if isinstance(m, AIMessage) and not m.content and m.tool_calls:
            m.content = "Consulting records..." 
        clean_messages.append(m)
    
    # B. Conversion Trigger (The "Closer")
    human_msg_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    # Trigger on 2nd message (early hook) and 5th message (deep hook)
    should_trigger_meeting = human_msg_count in [2, 5]
    
    conversion_text = ""
    if should_trigger_meeting:
        conversion_text = """
        [MANDATORY CLOSING]
        End response with exactly:
        
        "Would you like to schedule a call with one of our experts for a more detailed discussion?
        https://calendly.com/fandlgroupllc/30min"
        """

    # C. Dynamic Persona Prompt (With SMART FORMATTING)
    common_rules = """
    *** BRAND RULES ***
    1. **NO EMOJIS.** Be sophisticated, concise, and professional.
    2. **FORMATTING - CRITICAL:** - Use **Bullet Points** for services, steps, or options.
       - Keep responses **SHORT** (Max 2-3 sentences). 
       - No big paragraphs.
    3. **IDENTITY:** - You are 'LOFTY', a Digital Design Concierge (NOT an AI). 
       - Never say "As an AI language model".
    4. **FINANCING:** Only mention '8-Months Same-As-Cash'.
    5. **TIMELINE (STRICT):** NEVER guess specific weeks. ALWAYS say: "Timeline varies by project scope and complexity. We provide a detailed schedule during your consultation."
    6. **LOCATION:** We serve the **DMV area** (Washington DC, Maryland & Virginia).
    """

    if role == "realtor":
        system_prompt = f"""
        You are 'LOFTY', the Strategic Partner for Realtors & Investors at F&L Design Builders.
        Your goal: Help them sell faster and maximize ROI.
        
        KNOWLEDGE: {context}
        {common_rules}
        
        *** REALTOR PROTOCOL ***
        - **Services:** Focus on "Pre-Listing Refresh", "Quick Turnaround", "Curb Appeal".
        - **Partnership:** Emphasize the **1% Referral Commission**.
        - **Logic:** Do not ask about "feelings". Ask about "timeline to list" and "budget".
        
        {conversion_text}
        """
    else:
        system_prompt = f"""
        You are 'LOFTY', the Design Concierge for Homeowners at F&L Design Builders.
        Your goal: Guide them from Vision to Reality with White-Glove service.
        
        KNOWLEDGE: {context}
        {common_rules}
        
        *** CRITICAL INSTRUCTION ***
        - The retrieved documents contain a long 'Discovery Questionnaire'. **IGNORE IT.**
        - You must ONLY ask the 3 questions listed below in the exact order.
        - Do not ask about 'Atmosphere', 'Lifestyle', or 'Feng Shui' yet. That happens in the meeting.
        
        *** MANDATORY SCRIPT FLOW (Do not deviate) ***
        
        STEP 1: If user mentions a project (Kitchen, Bath, etc.), ASK exactly:
        "What kind of style do you envision for the space?
        * Modern
        * Traditional
        * Transitional
        * Contemporary"
        
        STEP 2: Once they answer the style, ASK exactly:
        "How soon are you looking to start this renovation? We can schedule a Project Manager to go deeper."
        
        STEP 3: Once they answer the timeline, SAY exactly:
        "Thank you. Let's schedule a meeting to discuss this in detail.
        Please choose a time here: https://calendly.com/fandlgroupllc/30min"
        
        *** SCENARIO HANDLING ***
        - **"Status/Login":** Use 'check_project_status' tool.
        - **"Speak to Human":** Use 'request_immediate_callback' tool.
        - **"Budget":** Mention 8-Month Financing.
        - **"Services/Mission":** Use bullet points to list services from Knowledge.
        
        {conversion_text}
        """

    final_input = [SystemMessage(content=system_prompt)] + clean_messages
    response = model.invoke(final_input)
    return {"messages": [response]}

def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    if state["messages"][-1].tool_calls:
        return "tools"
    return "__end__"

# --- 6. GRAPH CONSTRUCTION ---
workflow = StateGraph(AgentState)
workflow.add_node("classify", classify_user_node)
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
    async_pool = AsyncConnectionPool(conninfo=db_url, max_size=20, kwargs={"autocommit": True})
    await async_pool.open()
    checkpointer = AsyncPostgresSaver(async_pool)
    await checkpointer.setup()
    return workflow.compile(checkpointer=checkpointer)

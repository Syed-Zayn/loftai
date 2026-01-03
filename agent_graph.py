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
    
    # 1. MESSAGE SANITIZER
    clean_messages = []
    for m in messages:
        if isinstance(m, AIMessage) and not m.content and m.tool_calls:
            m.content = "Processing..." 
        clean_messages.append(m)
    
    # 2. ANALYZE CONVERSATION HISTORY (The "Brain" Check)
    # Hum check karenge ke Agent ka AAKHRI sawal kya tha.
    
    last_ai_msg = ""
    for m in reversed(clean_messages):
        if isinstance(m, AIMessage) and m.content:
            last_ai_msg = m.content.lower()
            break
            
    # --- DETERMINISTIC FLOW CONTROL (Python Logic > AI Guessing) ---
    
    # CASE A: Agar Agent ne pichli baar "Style" pucha tha -> Ab "Timeline" pucho.
    if "what kind of style" in last_ai_msg or "modern" in last_ai_msg and "?" in last_ai_msg:
        force_instruction = """
        [FLOW ENFORCED]
        The user just answered your question about 'Style'.
        Your ONLY goal now is to ask about the **Timeline**.
        
        Say exactly:
        "That is a wonderful choice.
        
        How soon are you looking to start this project?"
        """
        
    # CASE B: Agar Agent ne "Timeline" pucha tha -> Ab "Calendly" do.
    elif "how soon" in last_ai_msg or "start this project" in last_ai_msg:
        force_instruction = """
        [FLOW ENFORCED]
        The user just answered about the timeline.
        Your ONLY goal now is to **Close the Deal**.
        
        Say exactly:
        "Thank you. Let's schedule a meeting with our Project Manager to discuss this in detail.
        
        Please choose a time here: https://calendly.com/fandlgroupllc/30min"
        """
        
    # CASE C: Normal Conversation (Start or General Questions)
    else:
        # Default instructions (Start Script ONLY if intent detected)
        force_instruction = """
        [STANDARD MODE]
        Answer the user's question using the Retrieved Knowledge.
        
        *** CRITICAL TRIGGER ***
        IF (and ONLY IF) the user explicitly says they want to **start a project**, **renovate**, or **get a quote**:
        THEN ignore the general info and START THE SCRIPT:
        
        "It's wonderful you're considering a renovation! We can certainly help.
        
        To start, what kind of style do you envision for the space?
        * Modern
        * Traditional
        * Transitional
        * Contemporary"
        
        *** OTHERWISE ***
        Just answer their question normally (e.g., about Painting, Services, Financing).
        """

    # 3. CONSTRUCT SYSTEM PROMPT
    common_rules = """
    *** BRAND RULES ***
    1. **NO EMOJIS.** Be sophisticated.
    2. **FORMAT:** Use Vertical Bullet Points for options.
    3. **TIMELINE:** NEVER guess specific weeks.
    4. **FINANCING:** Mention '8-Months Same-As-Cash' if budget comes up.
    """

    if role == "realtor":
        system_prompt = f"""
        You are 'LOFTY' (Realtor Partner).
        Focus: ROI, Speed, 1% Commission.
        KNOWLEDGE: {context}
        {common_rules}
        """
    else:
        system_prompt = f"""
        You are 'LOFTY' (Homeowner Concierge).
        KNOWLEDGE: {context}
        {common_rules}
        
        {force_instruction}
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



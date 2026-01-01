import os
import asyncio
from typing import Annotated, Sequence, TypedDict, Literal
from dotenv import load_dotenv

# LangChain Imports
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_pinecone import PineconeVectorStore
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from langgraph.graph.message import add_messages 
from wix_client import WixManager
# Custom Modules
from hubspot_client import HubSpotManager
from quote_generator import QuoteGenerator

# 1. Environment & Setup
load_dotenv()
wix = WixManager()
hubspot = HubSpotManager()
pdf_engine = QuoteGenerator()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

# 2. Setup Pinecone (Connecting to the Knowledge Base)
print("🧠 Initializing AI Memory (Pinecone + Gemini)...")
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

# 3. Define Tools
from langchain_core.tools import tool

@tool
def save_lead_to_hubspot(name: str, email: str, phone: str):
    """
    Saves a new lead to HubSpot CRM AND Wix Newsletter.
    Use this immediately when the user provides their contact details.
    """
    status_msg = []
    
    # 1. Save to HubSpot (CRM)
    contact_id = hubspot.create_lead(name, email, phone)
    if "Error" in str(contact_id):
        status_msg.append(f"HubSpot Failed: {contact_id}")
    else:
        status_msg.append(f"HubSpot ID: {contact_id}")

    # 2. Save to Wix (Newsletter/Marketing)
    wix_success = wix.add_contact_to_wix(name, email, phone)
    if wix_success:
        status_msg.append("Added to Wix Newsletter")
    else:
        status_msg.append("Wix Sync Skipped")

    return f"Lead processed successfully: {', '.join(status_msg)}. Checklist sent to client."

@tool
def generate_quote_and_deal(project_type: str, budget: str, user_name: str, email: str, phone: str):
    """
    Generates a formal PDF quote and creates a HubSpot Deal.
    REQUIRED ARGUMENTS: Name, Email, Phone, Type (e.g., Kitchen), Budget.
    """
    # 1. Create Lead First
    contact_id = hubspot.create_lead(user_name, email, phone)
    wix.add_contact_to_wix(user_name, email, phone)
    
    # 2. Create Deal FIRST to get the ID
    deal_id = hubspot.create_deal_with_quote(contact_id, project_type, budget, "Generating...")
    
    if "Error" in str(deal_id):
        return f"Failed to create deal in HubSpot: {deal_id}"

    # 3. Generate PDF NOW (Using the Real Deal ID)
    try:
        result = pdf_engine.generate_pdf(user_name, project_type, budget, deal_id)
        
        if isinstance(result, tuple):
            pdf_path, pdf_filename = result
        else:
            pdf_path = result
            pdf_filename = os.path.basename(pdf_path)
            
        base_url = os.getenv("API_BASE_URL", "http://localhost:8000") 
        pdf_link = f"{base_url}/quotes/{pdf_filename}"
        
    except Exception as e:
        return f"Deal Created ({deal_id}), but PDF generation failed: {str(e)}"
    
    return f"Success! Deal {deal_id} Created. Quote Link: {pdf_link}"

@tool
def check_financing_eligibility(budget_concern: str):
    """
    Returns financing terms.
    Use ONLY when user mentions 'budget', 'cost', 'expensive', or 'payment plan'.
    """
    return "Eligible for: F&L Exclusive 8-Months Same-As-Cash Financing Program. (Approvals in minutes)."

@tool
def get_secure_upload_link():
    """
    Returns a secure link for the user to upload photos, videos, or measurements of their space.
    Use this when asking for site details.
    """
    return "Please upload your site photos and measurements securely here: https://forms.google.com/your-upload-form-link"

tools = [save_lead_to_hubspot, generate_quote_and_deal, check_financing_eligibility, get_secure_upload_link]

# 4. Initialize Model
model = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=GOOGLE_API_KEY,
    temperature=0.1 # Very low temp for strict formatting adherence
).bind_tools(tools)

# --- 5. UPDATED STATE & LOGIC ---

class AgentState(TypedDict):
    messages: Annotated[list, add_messages] 
    user_role: str # 'homeowner', 'realtor', or 'unknown'
    context: str

# NEW NODE: Classifier (Advanced Detection Logic)
def classify_user_node(state: AgentState):
    # If role is already set, don't re-classify
    if state.get("user_role") and state["user_role"] != "unknown":
        return {"user_role": state["user_role"]}
        
    last_msg = state["messages"][-1].content.lower()
    
    # Advanced Keyword Logic
    realtor_keywords = [
        "selling", "listing", "client", "market", "roi", "investor", "flip", 
        "broker", "agent", "commission", "pre-listing", "market value", "closing", "real estate"
    ]
    
    if any(x in last_msg for x in realtor_keywords):
        detected_role = "realtor"
        print("🕵️ Detected User Role: REALTOR/INVESTOR")
    else:
        # Default is homeowner
        detected_role = "homeowner"
        print("🏠 Detected User Role: HOMEOWNER")
        
    return {"user_role": detected_role}

def retrieve_node(state: AgentState):
    last_msg = state["messages"][-1]
    query = last_msg.content
    role = state.get("user_role", "homeowner")
    
    # Advanced Contextual Retrieval
    if role == "realtor":
        search_query = f"{query} services for realtors investors ROI renovation packages commission partnership pre-listing"
    else:
        search_query = f"{query} luxury home design renovation feng shui services process paint of hope venicasa lifestyle"
        
    print(f"🔍 Searching Knowledge Base for ({role}): {search_query}")
    docs = vectorstore.similarity_search(search_query, k=4) 
    context_text = "\n".join([d.page_content for d in docs])
    return {"context": context_text}

def generate_node(state: AgentState):
    context = state.get("context", "")
    role = state.get("user_role", "homeowner")
    messages = state["messages"]
    
    # --- 1. Message Sanitizer ---
    clean_messages = []
    for m in messages:
        if isinstance(m, AIMessage) and not m.content and m.tool_calls:
            m.content = "Processing request..." 
        clean_messages.append(m)
    
    last_msg_content = messages[-1].content if messages else ""
    
    # --- 2. DYNAMIC CONVERSION TRIGGER LOGIC (Updated Rule) ---
    # Trigger after 2-3 human messages
    human_msg_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    
    # Trigger the call to action on the 2nd message onwards
    should_trigger_meeting = human_msg_count >= 2
    
    conversion_instruction = ""
    if should_trigger_meeting:
        conversion_instruction = """
        [MANDATORY ACTION]
        At the end of your response, you MUST append this specific text exactly (on a new line):

        "Would you like to schedule a call with one of our experts for a more detailed discussion?
        https://calendly.com/fandlgroupllc/30min"
        """

    # --- 3. SECRET INTERNAL MODE (High-Level Strategic Partner) ---
    # Trigger: "FL_ADMIN_ACCESS" or "SECRET_KEY_786"
    if "FL_ADMIN_ACCESS" in last_msg_content or "SECRET_KEY_786" in last_msg_content:
        print("🔓 ADMIN MODE ACTIVATED")
        system_prompt = f"""
        You are the INTERNAL Strategic Operations Director for F&L Design Builders.
        Your goal is to provide high-level insights, draft operational comms, and analyze leads for Felicity & Lorena.
        
        INTERNAL KNOWLEDGE BASE: {context}
        
        YOUR EXECUTIVE CAPABILITIES:
        1. **Lead Analysis:** Summarize recent interactions, highlighting Budget, Timeline, and Sentiment.
        2. **Operational Support:** Draft professional internal emails to Project Managers (Crew) regarding site reports/updates.
        3. **Strategic Marketing:** Advise on leveraging 'Venicasa' (Furniture) or 'Paint of Hope' (Charity) in current campaigns.
        4. **Financial Extraction:** Extract and format budget details for the '8-Months Same-As-Cash' program.
        
        TONE: Direct, Analytical, Professional. STRICTLY USE BULLET POINTS for all data. No fluff.
        
        CRITICAL ACTION TRIGGER:
        - If the owner asks to generate a quote manually: 
          1. Ask for Client Name, Email, Phone, and Budget.
          2. THEN call the 'generate_quote_and_deal' tool immediately.
        """
        # Hide the secret key from the chat history passed to LLM
        if isinstance(clean_messages[-1], HumanMessage):
             clean_text = last_msg_content.replace("FL_ADMIN_ACCESS", "").replace("SECRET_KEY_786", "")
             clean_messages[-1] = HumanMessage(content=clean_text)

    else:
        # --- 4. ADVANCED CUSTOMER PERSONA PROMPTS (Strict Client Rules) ---
        
        business_rules = """
        *** CORE BUSINESS RULES & FACTS (ALWAYS TRUE) ***
        1. **Financing:** We offer "8-Months Same-As-Cash" financing. (NOT 6 or 12).
        2. **Charity:** We have a "Paint of Hope" initiative (Donation to charity with every project).
        3. **Furniture Partnership:** We have an exclusive partnership with "Venicasa" (Luxury European Furniture).
        4. **$300 Coupon:** Available for Homeowners ONLY. (Lead Magnet).
        5. **Realtor Commission:** We offer a 1% Referral Commission to partners (Code: 14F&L101).
        """

        base_prompt = f"""You are 'LOFTY', the Exclusive Design Concierge for F&L Design Builders.
        
        RETRIEVED CONTEXT:
        {context}
        
        {business_rules}
        
        *** STRICT STYLE & FORMATTING GUIDELINES (DO NOT VIOLATE) ***
        1. **LENGTH:** Keep your responses SHORT and CONCISE. 
           - Avoid long paragraphs.
           - Be direct and warm.
        
        2. **FORMATTING:** - If you mention more than one service or option, you MUST use bullet points.
           - Example Format:
             "We specialize in:
             * Service A
             * Service B"
        
        3. **TONE:** Personalized and Human. 
           - Do NOT sound like ChatGPT or a Robot.
           - Do NOT use formal "AI" language like "I can assist you with that." 
           - Instead say: "I'd love to help with that."
        
        4. **FORBIDDEN ITEMS:**
           - **NO EMOJIS.** (Strictly prohibited).
           - **NO MENTION OF "AI".** Never refer to yourself as an AI or bot.
           - **NO FILLER TEXT.** Do not say "Here is the information you requested." Just give the info.
        
        {conversion_instruction}
        """

        if role == "realtor":
            # REALTOR PERSONA
            persona_prompt = f"""
            {base_prompt}
            USER: REALTOR / INVESTOR.
            FOCUS: ROI, Speed, Market Value.
            
            OFFERS:
            * 1% Referral Commission
            * Pre-Listing Packages (Quick refresh)
            * Pay at Closing Options
            
            TONE: Professional, Brief, Business-like.
            """
        else:
            # HOMEOWNER PERSONA
            persona_prompt = f"""
            {base_prompt}
            USER: HOMEOWNER.
            FOCUS: Lifestyle, Vibe, "Personality & Lifestyle Intelligence™".
            
            *** DISCOVERY FLOW (One Question at a Time) ***:
            1. Ask about the "Atmosphere" they want (Calm? Energetic?).
            2. Ask about "Lifestyle" (Entertaining? Kids?).
            3. Ask about "Energy Flow" (Feng Shui).
            
            *** SCENARIO HANDLING ***:
            - **Services?** -> List them using bullet points.
            - **Quote?** -> "I can generate a preliminary quote. I just need a few details." (Call tool).
            - **Budget?** -> Mention "8-Months Same-As-Cash financing".
            - **Furniture?** -> Mention "Venicasa Partnership".
            """

        system_prompt = persona_prompt
    
    # --- 5. Final Execution ---
    final_input = [SystemMessage(content=system_prompt)] + clean_messages
    
    response = model.invoke(final_input)
    return {"messages": [response]}

tool_node = ToolNode(tools)

def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "tools"
    return "__end__"

# 6. Build Graph
workflow = StateGraph(AgentState)

# Add Nodes
workflow.add_node("classify", classify_user_node) # Step 1: Detect Role
workflow.add_node("retrieve", retrieve_node)      # Step 2: Get Info from Pinecone
workflow.add_node("agent", generate_node)         # Step 3: Generate Smart Answer
workflow.add_node("tools", tool_node)             # Step 4: Execute Tools

# Define Flow
workflow.add_edge(START, "classify")
workflow.add_edge("classify", "retrieve")
workflow.add_edge("retrieve", "agent")
workflow.add_conditional_edges("agent", should_continue)
workflow.add_edge("tools", "agent")

# 7. Compile with Robust Database Connection
async def get_app():
    db_url = os.getenv("NEON_DB_URL")
    
    # Connection Arguments (Optimized for Production)
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
        # --- NEW STABILITY SETTINGS ---
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

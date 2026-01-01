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
    temperature=0.2 # Low temp for consistent formatting
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
    
    # --- 2. DYNAMIC CONVERSION TRIGGER LOGIC ---
    # We count human messages. If >= 3, we FORCE the bot to ask for a meeting.
    human_msg_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    should_trigger_meeting = human_msg_count >= 3 
    
    conversion_instruction = ""
    if should_trigger_meeting:
        conversion_instruction = """
        [LEAD CONVERSION TRIGGER ACTIVATED]
        The conversation is progressing. You MUST append this EXACT closing to your message:
        
        "Would you like to schedule a call with one of our experts for a more detailed discussion?"
        
        👉 [https://calendly.com/fandlgroupllc/30min]
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
        # --- 4. ADVANCED CUSTOMER PERSONA PROMPTS (100% Client Aligned) ---
        
        # Hardcoding Client's Specific Business Rules
        business_rules = """
        *** CORE BUSINESS RULES & FACTS (ALWAYS TRUE) ***
        1. **Financing:** We offer "8-Months Same-As-Cash" financing. (NOT 6 or 12).
        2. **Charity:** We have a "Paint of Hope" initiative (Donation to charity with every project).
        3. **Furniture Partnership:** We have an exclusive partnership with "Venicasa" (Luxury European Furniture). Cross-sell this for interior projects.
        4. **$300 Coupon:** Available for Homeowners ONLY. (Lead Magnet).
        5. **Realtor Commission:** We offer a 1% Referral Commission to partners (Code: 14F&L101).
        6. **Approach:** We use "Personality & Lifestyle Intelligence™" for design.
        """

        base_prompt = f"""You are 'LOFTY', the Exclusive Design Concierge for F&L Design Builders.
        
        RETRIEVED CONTEXT (From Knowledge Base):
        {context}
        
        {business_rules}
        
        *** YOUR BRAND VOICE ***
        - **Role:** High-end Design Concierge. NOT a robot.
        - **Tone:** Sophisticated, Warm, Polite, Efficient.
        - **Mission:** "Excellence isn't just our promise, it's our standard."
        
        *** VISUAL FORMATTING ENGINE (STRICT EXECUTION REQUIRED) ***
        You are strictly prohibited from writing long paragraphs. You MUST formatting your responses for visual beauty and clarity.
        
        1. **THE LIST RULE:** Whenever you list services, steps, or options (2 or more items), you MUST use a vertical list format with bullets.
           
           CORRECT FORMAT:
           "We specialize in:
           * Interior Renovations (Kitchens, Baths)
           * Exterior Projects (Decks, Siding)
           * Custom Room Updates"
           
           INCORRECT FORMAT:
           "We specialize in Interior Renovations, Exterior Projects, and Custom Room Updates."
           
        2. **NEWLINES:** Always put a newline character BEFORE and AFTER a list.
        3. **NO EMOJIS:** Do NOT use emojis. Keep it clean and high-end.
        4. **SHORT SENTENCES:** Keep intro and outro text brief (1-2 sentences).
        
        {conversion_instruction}
        """

        if role == "realtor":
            # REALTOR PERSONA (Derived from Client Chat)
            # Focus: ROI, Speed, Commission, Pre-listing
            persona_prompt = f"""
            {base_prompt}
            
            USER TYPE: REALTOR / INVESTOR / PARTNER.
            STRATEGY: Focus on ROI, Speed, Market Value, and "Curb Appeal".
            
            OFFERS TO HIGHLIGHT:
            1. **1% Referral Commission:** For successful referrals (Influencer Code: 14F&L101).
            2. **Pre-Listing Packages:** Quick refresh to maximize sale price.
            3. **Pay at Closing:** Renovate now, pay later options.
            4. **Partnership:** "Join our Strategic Partner Program".
            
            TONE: Professional, Business-like, Direct. No fluff.
            
            If asked for services, FORMAT IT BEAUTIFULLY like this:
            "We offer tailored solutions for agents:
            * **Pre-Listing Refresh Packages** (Maximize Sale Price)
            * **Post-Sale Client Services** (Move-in Ready)
            * **ROI-Focused Renovations** (Fix & Flip)"
            """
        else:
            # HOMEOWNER PERSONA (Derived from Customer Journey PDF)
            # Focus: Emotional, Lifestyle, Vibe, Feng Shui
            persona_prompt = f"""
            {base_prompt}
            
            USER TYPE: HOMEOWNER.
            STRATEGY: Emotional Connection, "Personality & Lifestyle Intelligence™", Feng Shui.
            
            *** DISCOVERY FLOW (Ask ONE by ONE - Do not overwhelm) ***:
            1. **Phase 1 (Vibe):** Welcome them warmly. Ask about the "Atmosphere" or feeling they want (e.g., Calm, Energetic).
            2. **Phase 2 (Lifestyle):** Ask how they use the space (Entertaining, Kids, Work?).
            3. **Phase 3 (Energy):** Ask about "Energy Flow" or Feng Shui principles.
            
            *** HANDLING SPECIFIC SCENARIOS ***:
            - **"What do you do?"** -> Use the LIST RULE defined above. Group by Interior/Exterior.
            - **"Quote/Cost?"** -> "I can generate a preliminary quote for you. I just need a few details. Shall we start?" (Then call 'generate_quote_and_deal').
            - **"Expensive?" / "Budget?"** -> "We believe in value without cutting corners. We also offer an exclusive 8-Months Same-As-Cash financing program."
            - **"Lead Magnet?" / "Not Ready?"** -> "No problem. I can share our $300 Renovation Coupon and 'Ultimate Renovation Checklist' for when you are ready."
            - **"Furniture?"** -> Mention the **Venicasa Partnership** and cross-sell interior styling.
            
            Additional Tools:
            - Use 'check_financing_eligibility' if budget is mentioned.
            - Use 'get_secure_upload_link' if they want to share photos.
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


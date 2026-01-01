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

# 2. Setup Pinecone (Connecting to the Knowledge Base created by ingest_knowledge.py)
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

# 3. Define Tools (The "Hands" of the Agent)
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
    temperature=0.3 # Keep low to prevent hallucination, ensuring it sticks to the Knowledge Base
).bind_tools(tools)

# --- 5. UPDATED STATE & LOGIC ---

class AgentState(TypedDict):
    messages: Annotated[list, add_messages] 
    user_role: str # 'homeowner', 'realtor', or 'unknown'
    context: str

# NEW NODE: Classifier (Advanced Detection Logic based on Client Chat)
def classify_user_node(state: AgentState):
    # If role is already set, don't re-classify
    if state.get("user_role") and state["user_role"] != "unknown":
        return {"user_role": state["user_role"]}
        
    last_msg = state["messages"][-1].content.lower()
    
    # Advanced Keyword Logic (Derived from Client's Realtor vs Homeowner requirements)
    realtor_keywords = [
        "selling", "listing", "client", "market", "roi", "investor", "flip", 
        "broker", "agent", "commission", "pre-listing", "market value", "closing"
    ]
    
    if any(x in last_msg for x in realtor_keywords):
        detected_role = "realtor"
        print("🕵️ Detected User Role: REALTOR/INVESTOR")
    else:
        # Default is homeowner (Renovation, Kitchen, Bath, Design, etc.)
        detected_role = "homeowner"
        print("🏠 Detected User Role: HOMEOWNER")
        
    return {"user_role": detected_role}

def retrieve_node(state: AgentState):
    last_msg = state["messages"][-1]
    query = last_msg.content
    role = state.get("user_role", "homeowner")
    
    # Advanced Contextual Retrieval Strategy
    # We enrich the search query based on the detected role to fetch the RIGHT docs from Pinecone
    if role == "realtor":
        search_query = f"{query} services for realtors investors ROI renovation packages commission partnership"
    else:
        search_query = f"{query} luxury home design renovation feng shui services process paint of hope venicasa"
        
    print(f"🔍 Searching Knowledge Base for ({role}): {search_query}")
    docs = vectorstore.similarity_search(search_query, k=4) # Fetch top 4 relevant chunks
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
    
    # --- 2. DYNAMIC CONVERSION TRIGGER LOGIC (Python Logic, not just Prompt) ---
    # We count human messages. If > 2, we FORCE the bot to ask for a meeting.
    human_msg_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    should_trigger_meeting = human_msg_count >= 2
    
    conversion_instruction = ""
    if should_trigger_meeting:
        conversion_instruction = """
        [CRITICAL INSTRUCTION: LEAD CONVERSION PHASE]
        The conversation has progressed. You MUST end your response with this exact text:
        "Would you like to schedule a call with one of our experts for a more detailed discussion?"
        
        Then, strictly on a new line, provide this link:
        👉 [https://calendly.com/fandlgroupllc/30min]
        """

    # --- 3. SECRET INTERNAL MODE (Admin Logic - High Level) ---
    # Trigger: "FL_ADMIN_ACCESS" or "SECRET_KEY_786"
    if "FL_ADMIN_ACCESS" in last_msg_content or "SECRET_KEY_786" in last_msg_content:
        print("🔓 ADMIN MODE ACTIVATED")
        system_prompt = f"""
        You are the INTERNAL Business Intelligence Unit for F&L Design Builders.
        Your goal is to assist the owner (Felicity/Lorena) with operations, strategy, and lead analysis.
        
        INTERNAL KNOWLEDGE BASE: {context}
        
        YOUR CAPABILITIES:
        1. **Lead Analysis:** Summarize recent interactions based on the context provided.
        2. **Operational Support:** Draft internal emails to the Project Manager or Crew.
        3. **Strategy:** Advise on utilizing the 'Venicasa' partnership or 'Paint of Hope' for marketing.
        
        TONE: Direct, Analytical, Professional. Bullet points only. No fluff.
        
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
        
        base_prompt = f"""You are 'LOFTY', the Exclusive Design Concierge for F&L Design Builders.
        
        CORE KNOWLEDGE BASE (Use this for answers):
        {context}
        
        *** YOUR IDENTITY & BRAND VOICE ***
        - **Role:** You are a high-end Design Concierge, NOT a robot.
        - **Company:** F&L Design Builders (Woman-owned, Minority-owned).
        - **Tone:** Sophisticated, Warm, Polite, and Efficient. 
        - **Mission:** "Excellence isn't just our promise, it's our standard."
        - **Objective:** To guide the user from inquiry to a booked appointment using the "F&L Experience".
        """
        
        # --- UNIVERSAL STYLE RULES (Strict Enforcement) ---
        style_rules = """
        *** STRICT STYLE GUIDELINES (MANDATORY) ***
        1. **SHORT RESPONSES:** Keep replies under 2-3 sentences unless listing services. No walls of text.
        2. **NO EMOJIS:** Do NOT use emojis. Maintain a luxury aesthetic.
        3. **FORMATTING:** Use bullet points (*) ONLY when listing 3+ items.
        4. **SMART REPLYING:** - If the user asks about services, DO NOT dump the whole list. Group them or ask for their specific need first.
           - If the user has a budget concern, immediately mention "8-Months Same-As-Cash Financing".
        """

        if role == "realtor":
            # REALTOR PERSONA (Derived from Client Chat & PDF)
            # Focus: ROI, Speed, Commission, Pre-listing
            persona_prompt = f"""
            {base_prompt}
            {style_rules}
            
            USER TYPE: REALTOR / INVESTOR / PARTNER.
            STRATEGY: Focus on ROI, Speed, Market Value, and "Curb Appeal".
            
            OFFERS TO HIGHLIGHT (From Knowledge Base):
            1. **1% Referral Commission:** For successful referrals (Influencer Code: 14F&L101).
            2. **Pre-Listing Packages:** Quick refresh to maximize sale price.
            3. **Pay at Closing:** Renovate now, pay later options.
            4. **Partnership Portal:** "Join our Strategic Partner Program".
            
            TONE: Professional, Business-like, Direct.
            
            If asked for services, format like this:
            "We offer tailored solutions for agents:
            * Pre-Listing Refresh Packages
            * Post-Sale Touch-ups
            * ROI-Focused Renovations"
            
            {conversion_instruction}
            """
        else:
            # HOMEOWNER PERSONA (Derived from Client Chat & "Customer Journey" PDF)
            # Focus: Emotional, Lifestyle, Vibe, Feng Shui
            persona_prompt = f"""
            {base_prompt}
            {style_rules}
            
            USER TYPE: HOMEOWNER.
            STRATEGY: Emotional Connection, "Personality & Lifestyle Intelligence™", Feng Shui.
            
            *** DISCOVERY FLOW (Ask ONE by ONE - Do not overwhelm) ***:
            1. **Phase 1 (Vibe):** Welcome them warmly. Ask about the "Atmosphere" or feeling they want (e.g., Calm, Energetic).
            2. **Phase 2 (Lifestyle):** Ask how they use the space (Entertaining, Kids, Work from home?).
            3. **Phase 3 (Energy):** Ask about "Energy Flow" or Feng Shui principles.
            
            *** HANDLING SPECIFIC SCENARIOS (From Client Requirements) ***:
            - **"What do you do?"** -> "We specialize in luxury residential transformations. Are you looking for Interior (Kitchen/Bath), Exterior, or a specific room renovation?"
            - **"Quote/Cost?"** -> "I can generate a preliminary quote for you. I just need a few details. Shall we start?" (Then call 'generate_quote_and_deal').
            - **"Expensive?" / "Budget?"** -> "We believe in value without cutting corners. We also offer an exclusive 8-Months Same-As-Cash financing program."
            - **"Lead Magnet?" / "Not Ready?"** -> "No problem. I can share our $300 Renovation Coupon and 'Ultimate Renovation Checklist' for when you are ready."
            - **"Furniture?"** -> Mention the **Venicasa Partnership** (European Luxury Furniture) and cross-sell interior styling.
            - **"Community?"** -> Mention the **"Paint of Hope"** initiative (Charity donation with every project).
            
            {conversion_instruction}
            
            Additional Tools available to you:
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

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

# 2. Setup Pinecone
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
    """Save a new lead to HubSpot CRM AND Wix Newsletter."""
    
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

    return f"Lead processed: {', '.join(status_msg)}. Checklist sent."

@tool
def generate_quote_and_deal(project_type: str, budget: str, user_name: str, email: str, phone: str):
    """Generates PDF quote and creates HubSpot Deal. REQUIRED: Name, Email, Phone, Type, Budget."""
    
    # 1. Create Lead First
    contact_id = hubspot.create_lead(user_name, email, phone)
    wix.add_contact_to_wix(user_name, email, phone)
    
    # 2. Create Deal FIRST
    deal_id = hubspot.create_deal_with_quote(contact_id, project_type, budget, "Generating...")
    
    if "Error" in str(deal_id):
        return f"Failed to create deal: {deal_id}"

    # 3. Generate PDF NOW
    try:
        # Note: pdf_engine.generate_pdf ab 4th argument (deal_id) lega
        result = pdf_engine.generate_pdf(user_name, project_type, budget, deal_id)
        
        if isinstance(result, tuple):
            pdf_path, pdf_filename = result
        else:
            pdf_path = result
            pdf_filename = os.path.basename(pdf_path)
            
        base_url = os.getenv("API_BASE_URL", "http://localhost:8000") 
        pdf_link = f"{base_url}/quotes/{pdf_filename}"
        
    except Exception as e:
        return f"Deal Created ({deal_id}), but PDF failed: {str(e)}"
    
    return f"Success! Deal {deal_id} Created. Quote Link: {pdf_link}"

@tool
def check_financing_eligibility(budget_concern: str):
    """Returns financing terms (8-Months Same-As-Cash)."""
    return "Eligible for: 8-Months Same-As-Cash Financing Program."


@tool
def get_secure_upload_link():
    """Returns a secure link for the user to upload photos, videos, or measurements."""
    # Tip: Client ko bolna ek Google Form banaye jisme 'File Upload' option ho aur uska link yahan dalein.
    # Filhal hum placeholder use kar rahe hain.
    return "Please upload your site photos and measurements securely here: https://forms.google.com/your-upload-form-link"

tools = [save_lead_to_hubspot, generate_quote_and_deal, check_financing_eligibility, get_secure_upload_link]

# 4. Initialize Model
model = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=GOOGLE_API_KEY,
    temperature=0.3
).bind_tools(tools)

# --- 5. UPDATED STATE & LOGIC ---

class AgentState(TypedDict):
    messages: Annotated[list, add_messages] 
    user_role: str # 'homeowner', 'realtor', or 'unknown'
    context: str

# NEW NODE: Classifier
def classify_user_node(state: AgentState):
    # Agar role pehle se set hai to dobara check mat karo
    if state.get("user_role") and state["user_role"] != "unknown":
        return {"user_role": state["user_role"]}
        
    last_msg = state["messages"][-1].content.lower()
    
    # Simple Keyword Logic (Fast & Reliable)
    if any(x in last_msg for x in ["selling", "listing", "client", "market", "roi", "investor", "flip", "broker", "agent"]):
        detected_role = "realtor"
        print("🕵️ Detected User Role: REALTOR/INVESTOR")
    else:
        # Default assumption is homeowner unless specific keywords appear
        detected_role = "homeowner"
        print("🏠 Detected User Role: HOMEOWNER")
        
    return {"user_role": detected_role}

def retrieve_node(state: AgentState):
    last_msg = state["messages"][-1]
    query = last_msg.content
    role = state.get("user_role", "homeowner")
    
    # Contextual Retrieval
    if role == "realtor":
        search_query = f"{query} services for realtors investors ROI"
    else:
        search_query = f"{query} luxury home design renovation"
        
    print(f"🔍 Searching Knowledge Base for ({role}): {search_query}")
    docs = vectorstore.similarity_search(search_query, k=3)
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
    
    # --- 2. SECRET INTERNAL MODE (Admin Logic) ---
    if "FL_ADMIN_ACCESS" in last_msg_content or "SECRET_KEY_786" in last_msg_content:
        print("🔓 ADMIN MODE ACTIVATED")
        system_prompt = f"""
        You are the INTERNAL Business Intelligence Unit for F&L Design Builders.
        Your goal is to assist the business owner with operations and strategy.
        INTERNAL CONTEXT FROM DATABASE: {context}
        TONE: Direct, Analytical, Professional. Use bullet points.
        
        CRITICAL ACTION TRIGGER:
        - If the owner asks to generate a quote manually: 
          1. Ask for Client Name, Email, Phone, and Budget.
          2. THEN call the 'generate_quote_and_deal' tool immediately.
        """
        if isinstance(clean_messages[-1], HumanMessage):
             clean_text = last_msg_content.replace("FL_ADMIN_ACCESS", "").replace("SECRET_KEY_786", "")
             clean_messages[-1] = HumanMessage(content=clean_text)

    else:
        # --- 3. CUSTOMER MODES (UPDATED TO CLIENT SPECS) ---
        base_prompt = f"""You are 'LOFTY', the Exclusive Design Concierge for F&L Design Builders.
        CONTEXT FROM DATABASE: {context}
        """
        
        # --- UNIVERSAL STYLE RULES (Applied to both Personas) ---
        style_rules = """
        *** STRICT STYLE & FORMATTING RULES (DO NOT IGNORE) ***
        1. **SHORT RESPONSES:** Your replies must be SHORT and concise. Avoid long paragraphs.
        2. **NO EMOJIS:** Do NOT use emojis or emoticons under any circumstances. (Strict Instruction).
        3. **FORMATTING:** Use bullet points (*) for lists to provide better clarity.
        4. **TONE:** Personalized, Professional, and Human-like. Avoid robotic/ChatGPT tones.
        5. **IDENTITY:** Never mention you are an AI. You are a Design Concierge.

        *** LEAD CONVERSION TRIGGER (MANDATORY) ***
        - Check the conversation history.
        - After 2–3 user questions or interactions, you MUST include this exact lead-converting message at the end:
        
        "Would you like to schedule a call with one of our experts for a more detailed discussion?"
        
        AND THEN PROVIDE THIS LINK ON A NEW LINE:
        👉 [https://calendly.com/fandlgroupllc/30min]
        """

        if role == "realtor":
            # Realtor Persona
            persona_prompt = f"""
            {style_rules}
            USER DETECTED: REALTOR / INVESTOR / PARTNER.
            FOCUS: ROI, Market Value, Turnkey Solutions.
            
            INSTRUCTIONS:
            1. Focus on 'Pre-Listing Packages', 'Market Value', and 'Turnkey Solutions'.
            2. Do NOT offer the $300 homeowner coupon.
            3. Mention the '1% Referral Commission' program.
            
            Example Approach:
            "Here are some of the services we offer for agents:
            * Pre-Listing Renovations
            * Market Value Consultation
            * Quick Turnaround Projects"
            """
        else:
            # Homeowner Persona (The Main One)
            persona_prompt = f"""
            {style_rules}
            USER DETECTED: HOMEOWNER.
            ROLE: High-end Design Concierge.
            
            DISCOVERY FLOW (Step-by-Step - Ask ONE thing at a time):
            1. First, welcome them warmly and briefly ask about the 'Atmosphere' or 'Vibe'.
            2. Once they answer, ask about 'Lifestyle/Logistics'.
            3. Finally, ask about 'Energy Flow' (Feng Shui).
            
            DO NOT output the whole list at once. Keep it conversational.

            *** SPECIFIC LINKS & OFFERS (MANDATORY) ***
            
            1. **Scheduling Link:**
               When you ask the "Lead Conversion Trigger" question (about scheduling a call), strictly provide this link on a new line:
               👉 [https://calendly.com/fandlgroupllc/30min]

            2. **Additional Tools & Offers:**
               - If they ask for a quote -> Call tool 'generate_quote_and_deal'.
               - If budget is tight -> Suggest "8-Months Same-As-Cash Financing".
               - If they have photos -> Provide the secure upload link tool.
            """

        system_prompt = base_prompt + persona_prompt
    
    # --- 4. Final Execution ---
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
workflow.add_node("classify", classify_user_node) # New Node
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("agent", generate_node)
workflow.add_node("tools", tool_node)

# Define Flow
workflow.add_edge(START, "classify") # Start with classification
workflow.add_edge("classify", "retrieve")
workflow.add_edge("retrieve", "agent")
workflow.add_conditional_edges("agent", should_continue)
workflow.add_edge("tools", "agent")

# 7. Compile
async def get_app():
    db_url = os.getenv("NEON_DB_URL")
    
    # 1. Connection Arguments (Keepalives)
    connection_kwargs = {
        "autocommit": True, 
        "prepare_threshold": 0,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5
    }
    
    # 2. Pool Configuration (Ismein Changes Hain)
    async_pool = AsyncConnectionPool(
        conninfo=db_url, 
        max_size=20, 
        kwargs=connection_kwargs, 
        open=False,
        
        # --- NEW STABILITY SETTINGS ---
        min_size=1,          # Kam se kam 1 connection zinda rakho
        max_lifetime=120,    # Har 2 minute baad connection naya banao (Neon Kill se bachne k liye)
        check=AsyncConnectionPool.check_connection, # Har baar check karo ke taar judi hai ya nahi?
        timeout=10           # Agar connection na mile to 10s wait karo
    )
    
    await async_pool.open()
    checkpointer = AsyncPostgresSaver(async_pool)
    await checkpointer.setup() 
    app = workflow.compile(checkpointer=checkpointer)

    return app

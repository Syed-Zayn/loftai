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

# 3. Define Tools (Jo pehle thay, same)
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

    # 2. Save to Wix (Newsletter/Marketing) - NEW STEP
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
    
    # 2. Create Deal FIRST to get the ID (Abhi PDF link empty rahega)
    # Hum temporary link bhejenge, baad mein update bhi kar sakte hain
    deal_id = hubspot.create_deal_with_quote(contact_id, project_type, budget, "Generating...")
    
    if "Error" in str(deal_id):
        return f"Failed to create deal: {deal_id}"

    # 3. Generate PDF NOW (Using the Real Deal ID)
    try:
        # Note: pdf_engine.generate_pdf ab 4th argument (deal_id) lega
        result = pdf_engine.generate_pdf(user_name, project_type, budget, deal_id)
        
        if isinstance(result, tuple):
            pdf_path, pdf_filename = result
        else:
            pdf_path = result
            pdf_filename = os.path.basename(pdf_path)
            
        pdf_link = f"http://localhost:8000/quotes/{pdf_filename}" # Local link for testing
        
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

# NEW NODE: Classifier (Pehchano banda kaun hai)
def classify_user_node(state: AgentState):
    # Agar role pehle se set hai to dobara check mat karo
    if state.get("user_role") and state["user_role"] != "unknown":
        return {"user_role": state["user_role"]}
        
    last_msg = state["messages"][-1].content.lower()
    
    # Simple Keyword Logic (Fast & Reliable)
    # Production grade: You can use a small LLM call here too, but keywords are faster.
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
    # Agar Realtor hai to hum query mein 'business' keywords inject kar sakte hain
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
    
    # --- 1. Message Sanitizer (Old Code: Empty content fix) ---
    clean_messages = []
    for m in messages:
        if isinstance(m, AIMessage) and not m.content and m.tool_calls:
            m.content = "Processing..." 
        clean_messages.append(m)
    
    # Get the last user message content securely
    last_msg_content = messages[-1].content if messages else ""
    
    # --- 2. SECRET INTERNAL MODE (New Logic) ---
    # Agar message me ye secret code ho, to Internal Assistant ban jao
    if "FL_ADMIN_ACCESS" in last_msg_content or "SECRET_KEY_786" in last_msg_content:
        print("🔓 ADMIN MODE ACTIVATED")
        
        # Admin Prompt (Direct & Analytical)
        system_prompt = f"""
        You are the INTERNAL Business Assistant for F&L Design Builders.
        Your goal is to help the business owner, NOT a customer.
        
        INTERNAL CONTEXT FROM DATABASE: {context}
        
        Capabilities:
        1. Summarize recent leads or database info.
        2. Draft newsletters or internal emails.
        3. Explain the business strategy based on uploaded PDFs.
        
        Tone: Direct, Analytical, Professional. No fluff. Do NOT act like 'LOFTY' the concierge.
        """
        
        # Secret Key ko user message se hata dein taake LLM confuse na ho
        # Note: Hum last message ko temporary modify kar rahe hain prompt injection ke liye
        if isinstance(clean_messages[-1], HumanMessage):
             clean_text = last_msg_content.replace("FL_ADMIN_ACCESS", "").replace("SECRET_KEY_786", "")
             clean_messages[-1] = HumanMessage(content=clean_text)

    else:
        # --- 3. NORMAL CUSTOMER MODES (Old Logic) ---
        base_prompt = f"""You are LOFTY, the AI Design Concierge for F&L Design Builders.
        CONTEXT FROM DATABASE: {context}
        """
        
        if role == "realtor":
            # Realtor Persona: Business-like, Efficient, ROI-focused
            persona_prompt = """
            USER DETECTED: REALTOR / INVESTOR / PARTNER.
            TONE: Professional, Efficient, Results-Oriented.
            
            SPECIFIC RULES:
            1. Focus on ROI, Speed, and 'Pre-Listing Packages'.
            2. Do NOT offer the $300 Coupon (that is for homeowners).
            3. Mention our '1% Referral Commission' program.
            4. Use terms like 'Curb Appeal', 'Market Value', 'Turnkey'.
            5. Keep answers concise.
            """
        else:
            # Homeowner Persona: Warm, Dreamy, Emotional
            persona_prompt = """
            USER DETECTED: HOMEOWNER.
            ROLE: You are 'LOFTY', the Luxury Design Concierge & Lifestyle Architect.
            TONE: Warm, Sophisticated, Empathetic, "White-Glove Service".
            
            YOUR GOAL: Conduct the full "F&L Personality & Lifestyle Intelligence™ Discovery" before quoting.
            
            CRITICAL DISCOVERY PROTOCOL (Follow this exact flow):
            
            PHASE 1: THE VISION & FEEL
            - Ask: "What atmosphere do you want to create? (e.g., Calm, Luxurious, Creative?)"
            - Ask: "How do you want to FEEL in the space once it's complete?"
            
            PHASE 2: LIFESTYLE INTELLIGENCE (The "F&L Difference")
            - Ask: "Do you entertain guests often or do you work from home?" (Important for layout)
            - Ask: "Do you have pets? How do they use the space?"
            - Ask: "How important is 'Energy Flow' (Feng Shui) to you?"
            - Ask: "What are your top 3 favorite colors/materials?"
            
            PHASE 3: PRACTICALS & DECISION
            - Ask: "Who else will be involved in the decision-making for this project?"
            - Ask about Budget, Timeline, and Measurements.
            
            OFFERS & RULES:
            - ALWAYS offer the $300 Renovation Coupon as a "Thank You" for sharing.
            - If they have photos, say: "I can analyze your space. Please use the upload link."
            - If budget is tight, suggest "8-Months Same-As-Cash Financing".
            - Keep responses short (under 3 sentences) but warm. Use emojis ✨ 🏡.
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
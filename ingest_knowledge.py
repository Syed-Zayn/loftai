import os
import time
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec

# 1. Load Environment Variables
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
INDEX_NAME = "fl-builders-index"  # Pinecone mein ye index bana lena

# 2. Configure High-Level Embeddings (3072 Dimensions)
print("⚙️ Configuring Gemini Embeddings (3072 Dims)...")
embeddings = GoogleGenerativeAIEmbeddings(
    model="gemini-embedding-001",
    google_api_key=GOOGLE_API_KEY,
    task_type="retrieval_document",
    # Important: 3072 dimensions for high accuracy
    # Note: Ensure your Pinecone index is created with metric='cosine' and dimension=3072
)

# 3. Critical: Metadata Injection Logic
# Hum har file ke liye specific tags lagayenge taake AI confuse na ho
def load_and_tag_documents():
    documents = []
    
    # --- File A: Presentation (Brand Voice & Luxury) ---
    print("📂 Loading Presentation PDF...")
    try:
        loader = PyPDFLoader("F and L Design Builders Presentation (1).pdf")
        docs = loader.load()
        for doc in docs:
            doc.metadata.update({
                "source": "presentation",
                "type": "brand_voice",
                "topic": "luxury_identity",
                "verified": True
            })
        documents.extend(docs)
    except Exception as e:
        print(f"⚠️ Error loading Presentation: {e}")

    # --- File B: Customer Journey (Process Steps) ---
    print("📂 Loading Customer Journey PDF...")
    try:
        loader = PyPDFLoader("FANDL DIGITAL _ AI _CUSTOMER JOURNEY AND FANDL STEP BY STEP PROCESS 2026.pdf")
        docs = loader.load()
        for doc in docs:
            doc.metadata.update({
                "source": "customer_journey",
                "type": "process",
                "topic": "steps",
                "verified": True
            })
        documents.extend(docs)
    except Exception as e:
        print(f"⚠️ Error loading Journey PDF: {e}")

    # --- File C: Discovery Questionnaire (Lead Gen Script) ---
    print("📂 Loading Questionnaire DOCX...")
    try:
        loader = Docx2txtLoader("F&L Design Builders – Discovery Call Questionnaire.docx")
        docs = loader.load()
        for doc in docs:
            doc.metadata.update({
                "source": "questionnaire",
                "type": "script",
                "role": "homeowner_discovery",
                "verified": True
            })
        documents.extend(docs)
    except Exception as e:
        print(f"⚠️ Error loading Questionnaire: {e}")

    return documents

# 4. Critical: Financing Rule Override (Hardcoded Knowledge)
# Client ne chat mein '8 Months' kaha tha, jabke PDF mein '6 Months' ho sakta hai.
# Hum is rule ko alag se inject karenge.
def create_business_rules():
    from langchain_core.documents import Document
    
    # Hum specific rules inject kar rahe hain taake AI confuse na ho
    rules = [
        # --- GENERAL RULES ---
        Document(
            page_content="F&L Design Builders Financing Option: We offer an exclusive 8-Months Same-As-Cash financing program. Approvals take minutes. Do NOT mention 6 or 12 months.",
            metadata={"source": "hardcoded_rule", "topic": "financing", "role": "all", "verified": True}
        ),
        
        # --- HOMEOWNER RULES (Emotional) ---
        Document(
            page_content="For Homeowners: Focus on 'Vision', 'Dreams', and 'Lifestyle'. Always offer the $300 Design Coupon as a lead magnet. Use warm, supportive tone.",
            metadata={"source": "blueprint_strategy", "topic": "sales_psychology", "role": "homeowner", "verified": True}
        ),
        
        # --- REALTOR / INVESTOR RULES (Business/ROI) ---
        Document(
            page_content="For Realtors & Investors: Focus on 'ROI', 'Speed', 'Curb Appeal', and 'Pre-Listing Packages'. Do NOT offer the $300 coupon to them. Instead, offer 'Priority Scheduling' and 'Partner Referral Commission' (1% on closed deals). Talk efficiently and professionally.",
            metadata={"source": "blueprint_strategy", "topic": "sales_psychology", "role": "realtor", "verified": True}
        ),
        Document(
            page_content="Realtor Pre-Listing Package: Includes quick paint refresh, lighting updates, and minor repairs to maximize sale price. Completed in under 2 weeks.",
            metadata={"source": "services_list", "topic": "services", "role": "realtor", "verified": True}
        ),
        
        # --- PARTNERSHIPS ---
        Document(
            page_content="Partnership with Venicasa: Exclusive collaboration for Luxury Italian Furniture. We cross-sell furniture during interior design projects.",
            metadata={"source": "chat_strategy", "topic": "partnerships", "brand": "venicasa", "verified": True}
        )
    ]
    return rules

# 5. Main Execution
def ingest_data():
    # A. Load Files
    file_docs = load_and_tag_documents()
    
    # B. Load Rules
    rule_docs = create_business_rules()
    
    all_docs = file_docs + rule_docs
    
    # C. Split Text (Chunking)
    # Chunk size chota rakhenge taake specific answers milein
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        separators=["\n\n", "\n", " ", ""]
    )
    
    print(f"✂️ Splitting {len(all_docs)} documents into chunks...")
    splits = text_splitter.split_documents(all_docs)
    print(f"✅ Created {len(splits)} chunks.")

    # D. Upload to Pinecone
    print("🚀 Uploading to Pinecone Vector Store...")
    
    # Initializing Pinecone Client
    pc = Pinecone(api_key=PINECONE_API_KEY)
    
    # Check if index exists, create if not (Serverless)
    existing_indexes = [index.name for index in pc.list_indexes()]
    if INDEX_NAME not in existing_indexes:
        print(f"Creating index {INDEX_NAME}...")
        pc.create_index(
            name=INDEX_NAME,
            dimension=3072, # MUST MATCH EMBEDDINGS
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1")
        )
        time.sleep(2) # Wait for initialization

    # Upload
    vectorstore = PineconeVectorStore.from_documents(
        documents=splits,
        embedding=embeddings,
        index_name=INDEX_NAME
    )
    
    print("🎉 SUCCESS: All knowledge ingested into the Brain!")

if __name__ == "__main__":
    ingest_data()
import os
import time
import requests
import schedule
import re
from dotenv import load_dotenv

# Load Environment Variables
load_dotenv()

# --- CONFIGURATION ---
TRELLO_API_KEY = os.getenv("TRELLO_API_KEY")
TRELLO_TOKEN = os.getenv("TRELLO_TOKEN")
BOARD_ID = os.getenv("TRELLO_BOARD_ID")
IG_USER_ID = os.getenv("INSTAGRAM_BUSINESS_ID")
ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")

# Trello Lists
LIST_READY = "Ready to Post"
LIST_DONE = "Posted"

# --- 1. TRELLO HELPER FUNCTIONS ---
def get_trello_cards(list_name):
    if not TRELLO_API_KEY or not BOARD_ID:
        print("❌ CRITICAL ERROR: Trello API Key/ID Missing.")
        return []
    try:
        query = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN}
        lists = requests.get(f"https://api.trello.com/1/boards/{BOARD_ID}/lists", params=query).json()
        target_id = next((l['id'] for l in lists if l['name'].lower() == list_name.lower()), None)
        if not target_id: return []
        return requests.get(f"https://api.trello.com/1/lists/{target_id}/cards", params=query).json()
    except Exception as e:
        print(f"⚠️ Trello Error: {e}")
        return []

def move_card_to_list(card_id, target_list_name):
    try:
        query = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN}
        lists = requests.get(f"https://api.trello.com/1/boards/{BOARD_ID}/lists", params=query).json()
        target_id = next((l['id'] for l in lists if l['name'].lower() == target_list_name.lower()), None)
        if target_id:
            requests.put(f"https://api.trello.com/1/cards/{card_id}", params={**query, 'idList': target_id})
            print(f"✅ Card moved to '{target_list_name}'")
    except: pass

# --- 2. META GRAPH API POSTING ---
def post_to_instagram_api(image_url, caption):
    print("\n--- 📤 SENDING TO META ---")
    
    # --- ULTRA CLEAN URL EXTRACTION ---
    # Ye regex brackets [] () aur spaces ko link ka dushman samajhta hai aur wahin ruk jata hai.
    url_match = re.search(r'(https?://[^\s\[\]()]+)', image_url)
    
    if url_match:
        clean_url = url_match.group(1)
        print(f"🔹 Raw Trello Desc: {image_url[:50]}...")
        print(f"🔹 Cleaned URL: {clean_url}")
    else:
        print("❌ Error: Valid Image URL not found in description.")
        return False

    # Step 1: Create Container
    url_create = f"https://graph.facebook.com/v18.0/{IG_USER_ID}/media"
    payload_create = {
        'image_url': clean_url,
        'caption': caption,
        'access_token': ACCESS_TOKEN
    }
    
    try:
        response = requests.post(url_create, data=payload_create)
        result = response.json()
        
        if 'id' not in result:
            print(f"❌ Error Creating Container: {result}")
            return False
            
        creation_id = result['id']
        print(f"✅ Container Created ID: {creation_id}")
        
        # Wait for Meta to process
        print("⏳ Waiting 10 seconds for Meta to process...")
        time.sleep(10) # Thora zyada time diya safety ke liye
        
        # Step 2: Publish
        url_publish = f"https://graph.facebook.com/v18.0/{IG_USER_ID}/media_publish"
        payload_publish = {
            'creation_id': creation_id,
            'access_token': ACCESS_TOKEN
        }
        
        publish_response = requests.post(url_publish, data=payload_publish)
        publish_result = publish_response.json()
        
        if 'id' in publish_result:
            print(f"🎉 SUCCESS! Posted ID: {publish_result['id']}")
            return True
        else:
            print(f"❌ Error Publishing: {publish_result}")
            return False
            
    except Exception as e:
        print(f"⚠️ API Exception: {e}")
        return False

# --- 3. MAIN WORKFLOW ---
def process_trello_queue():
    print("\n" + "="*40)
    print("📅 CHECKING TRELLO FOR NEW POSTS...")
    cards = get_trello_cards(LIST_READY)
    
    if not cards:
        print("ℹ️ No cards found in 'Ready to Post'.")
        return

    for card in cards:
        print(f"\n🚀 Processing: {card['name']}")
        
        caption = card['name']
        raw_desc = card['desc'].strip()
        
        if not raw_desc:
            print("⚠️ Description empty. Skipping.")
            continue
            
        success = post_to_instagram_api(raw_desc, caption)
        
        if success:
            move_card_to_list(card['id'], LIST_DONE)
        else:
            print("⚠️ Keeping card in queue.")

# --- 4. SCHEDULER ---
if __name__ == "__main__":
    process_trello_queue()
    schedule.every(1).hours.do(process_trello_queue)
    print("\n🕒 Scheduler Started...")
    while True:
        schedule.run_pending()
        time.sleep(60)
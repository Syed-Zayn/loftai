import time
import random
import requests
import os
import pickle
import base64
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
# --- CONFIGURATION ---
FB_EMAIL = os.getenv("FB_EMAIL")
FB_PASSWORD = "ALIAS_ACCOUNT_PASSWORD"
API_URL = "http://localhost:8000/chat"
COOKIE_FILE = "fb_cookies.pkl"

# Telegram Config (Client ko phone par alert bhejne ke liye)
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN" # @BotFather se lein
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"     # @userinfobot se lein

# Target Groups & Keywords
TARGET_GROUPS = [
    "https://www.facebook.com/groups/bethesdamoms",
    "https://www.facebook.com/groups/potomaccommunity",
    # Add more URLs here
]

KEYWORDS = ["plumber", "painter", "renovation", "contractor", "kitchen", "bathroom", "remodel", "handyman"]

# --- 1. BROWSER SETUP (DESKTOP MODE) ---
def setup_browser():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications") # Facebook popups block karne ke liye
    
    # Cloud/Railway Headless Check
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("HEADLESS_MODE"):
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)
    return driver

# --- 2. AI & ALERTS ---
def get_ai_recommendation(post_text):
    try:
        # Prompt: Alias account ki taraf se natural recommendation
        prompt = f"Write a short, natural Facebook comment from a happy customer recommending 'F&L Design Builders'. Context: Someone asked: '{post_text[:100]}...'. Keep it casual, not salesy."
        
        payload = {
            "message": prompt,
            "session_id": "fb_group_spy",
            "platform": "bot_script"
        }
        response = requests.post(API_URL, json=payload, timeout=15)
        return response.json().get("response", "I highly recommend F&L Design Builders! They did great work for me.")
    except:
        return "I recommend F&L Design Builders!"

def send_telegram_alert(post_text, post_link, ai_reply):
    try:
        msg = f"🚨 **HOT LEAD DETECTED!**\n\n📝 **Post:** {post_text[:200]}...\n\n🔗 **Link:** {post_link}\n\n🤖 **Suggested Reply:**\n`{ai_reply}`"
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        requests.post(url, data=data)
        print("🔔 Alert Sent to Telegram!")
    except Exception as e:
        print(f"⚠️ Telegram Error: {e}")

# --- 3. LOGIN & COOKIES (The 'Set & Forget' Logic) ---
def login_facebook(driver):
    print("👤 Logging into Facebook...")
    driver.get("https://www.facebook.com/")
    time.sleep(3)

    # Load Cookies
    cookies_loaded = False
    if os.path.exists(COOKIE_FILE):
        try:
            cookies = pickle.load(open(COOKIE_FILE, "rb"))
            for cookie in cookies:
                try: driver.add_cookie(cookie)
                except: pass
            cookies_loaded = True
        except: pass
    elif os.getenv("FB_COOKIES_BASE64"):
        try:
            cookies = pickle.loads(base64.b64decode(os.getenv("FB_COOKIES_BASE64")))
            for cookie in cookies:
                try: driver.add_cookie(cookie)
                except: pass
            cookies_loaded = True
        except: pass

    if cookies_loaded:
        driver.refresh()
        time.sleep(5)
        if "login" not in driver.current_url:
            print("🎉 Auto-Login Successful via Cookies!")
            return

    # Manual Login
    print("ℹ️ Doing Fresh Login...")
    try:
        email_box = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "email")))
        pass_box = driver.find_element(By.ID, "pass")
        
        email_box.send_keys(FB_EMAIL)
        time.sleep(1)
        pass_box.send_keys(FB_PASSWORD)
        pass_box.send_keys(Keys.ENTER)
        
        time.sleep(10)
        # 2FA Check
        if "checkpoint" in driver.current_url:
            print("🚨 2FA DETECTED! Waiting 60s for approval...")
            time.sleep(60)
        
        # Save Cookies
        pickle.dump(driver.get_cookies(), open(COOKIE_FILE, "wb"))
        print("✅ Login Success & Cookies Saved!")
        
    except Exception as e:
        print(f"❌ Login Failed: {e}")



# --- 1. NEW HELPER FUNCTION (Comment Post Karne Ke Liye) ---
def post_comment_on_facebook(driver, comment_text):
    """
    Yeh function post ke niche comment box dhoondta hai aur AI reply type karke Enter dabata hai.
    """
    try:
        print("✍️ Attempting to Auto-Comment...")
        
        # 1. Comment Box dhoondna (FB classes change hoti hain, Aria-Label best hai)
        # Often input is inside a div with role='textbox' and contenteditable='true'
        try:
            comment_box = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//div[@role='textbox' and @contenteditable='true']"))
            )
        except:
            # Fallback strategy: Focus on the post and try to find comment button first if box is hidden
            print("⚠️ Box not found directly, trying generic approach...")
            return False
        
        # 2. Click to focus (Taake typing shuru ho sake)
        driver.execute_script("arguments[0].click();", comment_box)
        time.sleep(2)
        
        # 3. Human-like Typing (Safety ke liye ek ek harf type karega)
        for char in comment_text:
            comment_box.send_keys(char)
            time.sleep(random.uniform(0.05, 0.2)) # Random delay taake bot na lagay
            
        time.sleep(1)
        
        # 4. Press Enter to Post
        comment_box.send_keys(Keys.ENTER)
        print("✅ Comment Posted Successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Failed to Comment: {e}")
        return False

# --- 2. UPDATED MONITOR FUNCTION (Full Logic) ---
def monitor_groups(driver):
    print("\n🕵️ Starting Surveillance Cycle...")
    
    for group_url in TARGET_GROUPS:
        try:
            # Sorting by 'New Posts' often helps catch latest leads
            driver.get(group_url)
            time.sleep(random.randint(5, 8))
            
            print(f"👀 Scanning: {group_url.split('/')[-1]}")
            
            # Scrape Posts (Generic FB Feed Selector)
            posts = driver.find_elements(By.XPATH, "//div[@role='feed']//div[@data-ad-preview='message']")
            
            # Fallback if specific attribute fails
            if not posts:
                posts = driver.find_elements(By.XPATH, "//div[@role='article']")

            for post in posts[:5]: # Check top 5 latest
                try:
                    text = post.text.lower()
                    
                    # Keyword Matching
                    if any(word in text for word in KEYWORDS):
                        print(f"🔥 MATCH FOUND: {text[:50]}...")
                        
                        # Get Post Link
                        try:
                            link_elem = post.find_element(By.XPATH, ".//a[contains(@href, '/posts/') or contains(@href, '/permalink/')]")
                            post_link = link_elem.get_attribute("href")
                        except:
                            post_link = driver.current_url # Fallback
                        
                        # Generate AI Recommendation
                        print("🧠 Generating Strategy...")
                        ai_suggestion = get_ai_recommendation(text)
                        
                        # --- NEW ACTION LOGIC START ---
                        
                        # 1. Action: Post Comment Automatically
                        posted = post_comment_on_facebook(driver, ai_suggestion)
                        
                        # 2. Alert Logic based on Success/Failure
                        if posted:
                            # Sirf tab success alert bhejo jab post ho jaye
                            send_telegram_alert(text, post_link, f"✅ POSTED: {ai_suggestion}")
                            print("🔔 Success Alert Sent to Telegram!")
                        else:
                            # Agar fail ho jaye to alert bhejo taake human check kare
                            send_telegram_alert(text, post_link, f"⚠️ FAILED TO POST (Manual Check Needed): {ai_suggestion}")
                            print("🔔 Failure Alert Sent to Telegram!")
                        
                        # --- NEW ACTION LOGIC END ---
                        
                        time.sleep(random.randint(10, 20)) # Post karne ke baad thora lamba break
                        
                except Exception as e:
                    continue
                    
        except Exception as e:
            print(f"⚠️ Error scanning group: {e}")
            continue







# --- MAIN LOOP ---
if __name__ == "__main__":
    driver = setup_browser()
    login_facebook(driver)
    
    while True:
        monitor_groups(driver)
        
        # Human Behavior: Random long wait between scans
        wait_time = random.randint(1800, 3600) # 30-60 mins
        print(f"💤 Sleeping for {wait_time/60:.1f} minutes...")
        time.sleep(wait_time)
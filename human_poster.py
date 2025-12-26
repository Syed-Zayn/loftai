import time
import random
import os
import pickle
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Credentials
INSTA_USERNAME = os.getenv("INSTA_USERNAME")
INSTA_PASSWORD = os.getenv("INSTA_PASSWORD")
# ALIAS ACCOUNT CREDENTIALS (Client ko bolna yahan Alias use kare)
FB_EMAIL = os.getenv("FB_EMAIL")         
FB_PASSWORD = os.getenv("FB_PASSWORD")   

# Target Groups & Keywords (From Chat Strategy)
TARGET_GROUPS = [
    "https://www.facebook.com/groups/bethesdamoms",
    "https://www.facebook.com/groups/potomaccommunity",
    "https://www.facebook.com/groups/chevychasecommunity",
]

KEYWORDS = ["plumber", "painter", "renovation", "contractor", "kitchen", "bathroom", "remodel", "handyman", "builder"]

# --- BROWSER SETUP (Desktop Mode for Stability) ---
def setup_browser():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    # Standard User Agent to look human
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Optional: Headless mode for server (Make sure to test locally first)
    # options.add_argument("--headless") 
    
    driver = webdriver.Chrome(options=options)
    return driver

# ==========================================
# TASK 1: INSTAGRAM NEW FOLLOWER WELCOME
# (API ye nahi kar sakti, isliye Selenium zaroori hai)
# ==========================================
def process_new_followers(driver):
    logging.info("📸 Starting Instagram Follower Check...")
    try:
        driver.get("https://www.instagram.com/accounts/login/")
        time.sleep(5)
        
        # Login Logic
        try:
            u_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "username")))
            u_input.send_keys(INSTA_USERNAME)
            time.sleep(1)
            p_input = driver.find_element(By.NAME, "password")
            p_input.send_keys(INSTA_PASSWORD)
            p_input.send_keys(Keys.ENTER)
            time.sleep(8)
        except:
            logging.info("ℹ️ Already logged in or login skipped.")

        # Notifications Page
        driver.get("https://www.instagram.com/accounts/activity/")
        time.sleep(random.randint(6, 10))
        
        # Find 'started following you' notifications
        followers = driver.find_elements(By.XPATH, "//div[contains(text(), 'started following you')]")
        
        if not followers:
            logging.info("✅ No new followers detected.")
            return

        logging.info(f"🔥 Found {len(followers)} potential new followers.")
        
        # Limit to 3 DMs per run to stay safe
        for notification in followers[:3]:
            try:
                # Click Profile Picture/Name to go to profile
                parent = notification.find_element(By.XPATH, "./ancestor::a")
                profile_url = parent.get_attribute("href")
                
                driver.get(profile_url)
                time.sleep(random.randint(5, 8))
                
                # Click Message Button
                msg_btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//div[text()='Message']")))
                msg_btn.click()
                time.sleep(6)
                
                # Check if we already chatted (Look for bubbles)
                bubbles = driver.find_elements(By.XPATH, "//div[@role='row']")
                if len(bubbles) > 0:
                    logging.info("⏩ Conversation exists. Skipping DM.")
                    continue
                
                # Type Welcome Message (Human-like typing)
                welcome_msg = "Hi! Thank you for following F&L Design Builders. 🏠 Are you looking for design inspiration or planning a renovation soon? We'd love to help! - Lofty"
                
                box = driver.find_element(By.XPATH, "//div[@role='textbox']")
                for char in welcome_msg:
                    box.send_keys(char)
                    time.sleep(random.uniform(0.05, 0.15))
                box.send_keys(Keys.ENTER)
                
                logging.info(f"✅ Welcome DM sent to new follower!")
                time.sleep(random.randint(20, 40)) # Long pause between DMs
                
            except Exception as e:
                logging.error(f"⚠️ Failed to process a follower: {e}")
                continue

    except Exception as e:
        logging.error(f"❌ Instagram Module Error: {e}")

# ==========================================
# TASK 2: FACEBOOK GROUP MONITOR (ALIAS)
# (Client Requirement: Join local groups and recommend F&L)
# ==========================================
def process_facebook_groups(driver):
    logging.info("📘 Starting Facebook Group Monitor (Alias Mode)...")
    try:
        driver.get("https://www.facebook.com/")
        time.sleep(3)
        
        # Login with ALIAS Account
        try:
            email_box = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "email")))
            email_box.send_keys(FB_EMAIL)
            pass_box = driver.find_element(By.ID, "pass")
            pass_box.send_keys(FB_PASSWORD)
            pass_box.send_keys(Keys.ENTER)
            time.sleep(10)
        except:
            logging.info("ℹ️ Login skipped or failed.")

        for group_url in TARGET_GROUPS:
            try:
                logging.info(f"👀 Scanning Group: {group_url}")
                driver.get(group_url)
                time.sleep(random.randint(8, 12))
                
                # Scroll down to load posts
                driver.execute_script("window.scrollTo(0, 1000);")
                time.sleep(5)
                
                # Find Posts (Generic Selector)
                posts = driver.find_elements(By.XPATH, "//div[@role='article']")
                
                for post in posts[:5]: # Check top 5 posts
                    text = post.text.lower()
                    
                    if any(kw in text for kw in KEYWORDS):
                        logging.info(f"🎯 LEAD FOUND: {text[:50]}...")
                        
                        # Recommendation Text
                        recommendation = "I highly recommend F&L Design Builders! They did an amazing job on my renovation. Very professional and luxury finish. Check them out!"
                        
                        # Try to Comment
                        try:
                            # Finding the comment button/box is tricky on FB, usually aria-label helps
                            comment_box = post.find_element(By.XPATH, ".//div[@aria-label='Write a comment' or @role='textbox']")
                            driver.execute_script("arguments[0].click();", comment_box)
                            time.sleep(2)
                            
                            active_el = driver.switch_to.active_element
                            for char in recommendation:
                                active_el.send_keys(char)
                                time.sleep(random.uniform(0.05, 0.2))
                            
                            active_el.send_keys(Keys.ENTER)
                            logging.info("✅ Recommendation Posted (Alias Account).")
                            
                            # STOP after one recommendation to avoid spamming
                            return 
                        except:
                            logging.warning("⚠️ Could not find comment box.")
            
            except Exception as e:
                logging.error(f"⚠️ Error scanning group {group_url}: {e}")

    except Exception as e:
        logging.error(f"❌ Facebook Module Error: {e}")

# ==========================================
# MAIN CONTROLLER
# ==========================================
if __name__ == "__main__":
    print("🚀 Starting F&L Stealth Bot (Part B)...")
    print("ℹ️ NOTE: This script handles New Followers & Group Monitoring ONLY.")
    print("ℹ️ NOTE: Auto-Replies are handled by the API Server.")
    
    driver = setup_browser()
    
    try:
        # 1. Instagram Check
        process_new_followers(driver)
        
        # Clear cookies to switch context safely
        driver.delete_all_cookies()
        time.sleep(5)
        
        # 2. Facebook Group Check
        process_facebook_groups(driver)
        
    finally:
        print("🛑 Tasks Complete. Closing Browser.")
        driver.quit()
import os
import re
import time
import requests
from hubspot import HubSpot
from hubspot.crm.contacts import SimplePublicObjectInput as ContactInput
from hubspot.crm.deals import SimplePublicObjectInput as DealInput
from dotenv import load_dotenv

load_dotenv()

HUBSPOT_ACCESS_TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN")

class HubSpotManager:
    def __init__(self):
        if not HUBSPOT_ACCESS_TOKEN:
            print("⚠️ HubSpot Token not found! CRM sync will be simulated.")
            self.client = None
        else:
            self.client = HubSpot(access_token=HUBSPOT_ACCESS_TOKEN)

    def clean_budget(self, amount_str):
        """
        Converts inputs like '$50k', '50,000' into pure numbers.
        """
        try:
            clean_str = str(amount_str).lower().replace(",", "").strip()
            multiplier = 1
            if 'k' in clean_str: multiplier = 1000
            elif 'm' in clean_str: multiplier = 1000000
            
            matches = re.findall(r"(\d+\.?\d*)", clean_str)
            if matches:
                val = float(matches[0]) * multiplier
                return str(val)
            return "0.00"
        except:
            return "0.00"

    def create_lead(self, name: str, email: str, phone: str):
        if not self.client: return "simulated_contact_id_123"

        try:
            name_parts = name.strip().split(" ")
            first_name = name_parts[0]
            last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

            properties = {
                "email": email,
                "firstname": first_name,
                "lastname": last_name,
                "phone": phone,
                "lifecyclestage": "lead"
            }
            contact_input = ContactInput(properties=properties)
            response = self.client.crm.contacts.basic_api.create(
                simple_public_object_input_for_create=contact_input
            )
            return response.id

        except Exception as e:
            error_msg = str(e)
            if "409" in error_msg or "Existing ID" in error_msg:
                print("ℹ️ Contact already exists. Fetching existing ID...")
                match = re.search(r"Existing ID: (\d+)", error_msg)
                if match: return match.group(1)
            
            print(f"⚠️ HubSpot Contact Error: {e}")
            return f"existing_user_{email}"

    def create_deal_with_quote(self, contact_id, project_type, budget, quote_link):
        if not self.client: return "simulated_deal_id_999"

        final_amount = self.clean_budget(budget)
        print(f"💰 Cleaned Budget: {final_amount}")

        try:
            # 1. Create the Deal
            properties = {
                "dealname": f"{project_type} Renovation",
                "amount": final_amount,
                "dealstage": "appointmentscheduled", 
                "description": f"AI Generated Quote: {quote_link}\nIncludes 8-Month Financing Option."
            }
            deal_input = DealInput(properties=properties)
            deal_resp = self.client.crm.deals.basic_api.create(
                simple_public_object_input_for_create=deal_input
            )
            
            # 2. Associate Deal with Contact
            if contact_id and contact_id.isdigit():
                try:
                    # New Method: Use Associations API V4
                    # Association Type ID 3 = Deal to Contact (Standard HubSpot)
                    self.client.crm.associations.v4.basic_api.create(
                        object_type="deals",
                        object_id=deal_resp.id,
                        to_object_type="contacts",
                        to_object_id=contact_id,
                        association_spec=[{
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 3 
                        }]
                    )
                    print(f"✅ Deal {deal_resp.id} linked to Contact {contact_id}")
                except Exception as assoc_error:
                    print(f"⚠️ Association Warning (Deal created but not linked): {assoc_error}")
            
            return deal_resp.id

        except Exception as e:
            print(f"⚠️ HubSpot Deal Error: {e}")
            return f"Error creating deal: {str(e)}"

    # --- NEW FUNCTIONS FOR QUOTE FEEDBACK LOOP ---

    def update_deal_stage(self, deal_id, stage_id):
        """
        Updates the deal stage (e.g., 'closedwon', 'closedlost').
        """
        if not self.client: return False
        
        try:
            properties = {
                "dealstage": stage_id
            }
            simple_public_object_input = DealInput(properties=properties)
            self.client.crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input=simple_public_object_input
            )
            print(f"✅ Deal {deal_id} updated to stage: {stage_id}")
            return True
        except Exception as e:
            print(f"❌ Error updating deal {deal_id}: {str(e)}")
            return False

    def add_note_to_deal(self, deal_id, note_content):
        """
        Adds a note to the deal (used for Reject Reasons).
        Using direct API requests for better association handling.
        """
        if not self.client: return False
        
        url = "https://api.hubapi.com/crm/v3/objects/notes"
        headers = {
            'Authorization': f'Bearer {HUBSPOT_ACCESS_TOKEN}',
            'Content-Type': 'application/json'
        }
        
        # Note body with association to Deal
        data = {
            "properties": {
                "hs_timestamp": str(int(time.time() * 1000)),
                "hs_note_body": note_content
            },
            "associations": [
                {
                    "to": {"id": deal_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 214}] # 214 is Note-to-Deal
                }
            ]
        }
        
        try:
            response = requests.post(url, headers=headers, json=data)
            if response.status_code in [200, 201]:
                print(f"📝 Note added to Deal {deal_id}: {note_content}")
                return True
            else:
                print(f"⚠️ Failed to add note: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Error adding note: {e}")
            return False
    # --- NEW FUNCTION FOR CLIENT PORTAL (PROBLEM 2) ---
    def get_deal_by_email(self, email):
        """
        Wix Portal ke liye: Email se Contact dhoondta hai aur associated Deal/Project ka data lata hai.
        """
        if not self.client: 
            # Simulation Mode (Agar token na ho to ye fake data dega testing k liye)
            return {"project": "Luxury Kitchen (Demo)", "status": "In Progress", "link": "#"}
        
        try:
            # 1. Search Contact by Email
            filter_group = {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
            search_request = {"filterGroups": [filter_group], "properties": ["firstname", "lastname"]}
            
            contact_result = self.client.crm.contacts.search_api.do_search(public_object_search_request=search_request)
            
            if contact_result.total == 0:
                return None
            
            contact_id = contact_result.results[0].id
            
            # 2. Get Associated Deals
            # Hum latest deal uthayenge jo is contact ke sath jurri hui hai
            associations = self.client.crm.associations.v4.basic_api.get_page(
                object_type="contacts", object_id=contact_id, to_object_type="deals"
            )
            
            if not associations.results:
                return {"project": "No Active Project", "status": "Pending", "link": ""}

            latest_deal_id = associations.results[0].to_object_id
            
            # 3. Get Deal Details
            deal = self.client.crm.deals.basic_api.get_by_id(
                deal_id=latest_deal_id,
                properties=["dealname", "dealstage", "description", "amount"]
            )
            
            # Extract Drive Link (Hum assume kr rhy hain link description mein hoga ya hum default drive link denge)
            drive_link = "https://drive.google.com/" 
            # Agar description mein koi link hua to wo extract kr skty hain future mein
            
            return {
                "project": deal.properties['dealname'],
                "status": deal.properties['dealstage'], # Ye internal ID hogi (e.g., appointmentscheduled)
                "amount": deal.properties['amount'],
                "link": drive_link
            }
            
        except Exception as e:
            print(f"⚠️ Portal Fetch Error: {e}")
            return None
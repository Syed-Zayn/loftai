import os
import re
import time
import requests
from hubspot import HubSpot
from hubspot.crm.contacts import SimplePublicObjectInput as ContactInput
from hubspot.crm.deals import SimplePublicObjectInput as DealInput
from hubspot.crm.contacts.exceptions import ApiException
from hubspot.crm.deals.exceptions import ApiException as DealApiException
from dotenv import load_dotenv

# 1. Load Environment Variables
load_dotenv()

HUBSPOT_ACCESS_TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN")

class HubSpotManager:
    def __init__(self):
        # Check if Token exists
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

    # --- CRITICAL NEW FUNCTION: Force Update Contact ---
    def update_contact_details(self, contact_id, name, phone):
        """
        Updates an existing contact's Name and Phone.
        This fixes the issue where 'Website Visitor' was not changing to the Real Name.
        """
        if not self.client: return
        
        try:
            # Split Name into First and Last
            name_parts = name.strip().split(" ")
            first_name = name_parts[0]
            last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
            
            properties = {
                "firstname": first_name,
                "lastname": last_name,
                "phone": phone
            }
            
            contact_input = ContactInput(properties=properties)
            
            # API Call to Update
            self.client.crm.contacts.basic_api.update(
                contact_id=contact_id,
                simple_public_object_input=contact_input
            )
            print(f"🔄 HubSpot Contact Updated: {contact_id} -> {name} | {phone}")
        except Exception as e:
            print(f"⚠️ Update Failed: {e}")

    def get_contact_id_by_email(self, email):
        """Helper to find a contact ID if they already exist."""
        if not self.client: return None
        try:
            public_object_search_request = {
                "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
                "properties": ["id"],
                "limit": 1
            }
            result = self.client.crm.contacts.search_api.do_search(
                public_object_search_request=public_object_search_request
            )
            if result.results:
                return result.results[0].id
            return None
        except Exception as e:
            print(f"❌ Search Error: {e}")
            return None

    def create_or_update_contact(self, email: str, **kwargs):
        """
        Wrapper to ensure compatibility with other files using this name.
        Redirects to create_lead logic.
        """
        name = kwargs.get("name", "")
        if not name:
             name = f"{kwargs.get('firstname', '')} {kwargs.get('lastname', '')}".strip()
        
        return self.create_lead(name, email, kwargs.get("phone", ""))

    def create_lead(self, name: str, email: str, phone: str):
        """
        Creates a new lead. 
        CRITICAL FIX: If email exists (409 Error), it UPDATES the Name and Phone.
        """
        if not self.client: return "simulated_contact_id_123"

        try:
            # Prepare Data
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
            
            # Use ContactInput wrapper
            contact_input = ContactInput(properties=properties)
            
            # Try to CREATE
            response = self.client.crm.contacts.basic_api.create(
                simple_public_object_input_for_create=contact_input
            )
            print(f"✅ HubSpot Contact Created: {response.id}")
            return response.id

        except ApiException as e:
            # --- DUPLICATE HANDLING LOGIC ---
            # If Contact Already Exists (Error 409), UPDATE it instead of failing.
            if e.status == 409: 
                print(f"ℹ️ Contact {email} exists. Updating Name/Phone...")
                existing_id = self.get_contact_id_by_email(email)
                
                if existing_id:
                    # Force update the details
                    self.update_contact_details(existing_id, name, phone)
                    return existing_id
            
            print(f"⚠️ HubSpot Contact Error: {e}")
            # Return a dummy string so the bot doesn't crash
            return f"existing_user_{email}"
            
        except Exception as e:
            print(f"⚠️ HubSpot Unexpected Error: {e}")
            return f"error_{email}"

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
                "pipeline": "default",
                "description": f"AI Generated Quote: {quote_link}\nIncludes 8-Month Financing Option."
            }
            deal_input = DealInput(properties=properties)
            
            deal_resp = self.client.crm.deals.basic_api.create(
                simple_public_object_input_for_create=deal_input
            )
            
            # 2. Associate Deal with Contact
            if contact_id and str(contact_id).isdigit():
                try:
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
                    print(f"⚠️ Association Warning: {assoc_error}")
            
            return deal_resp.id

        except Exception as e:
            print(f"⚠️ HubSpot Deal Error: {e}")
            return f"Error creating deal: {str(e)}"

    # --- QUOTE FEEDBACK LOOP FUNCTIONS ---

    def update_deal_stage(self, deal_id, stage_id):
        """
        Updates the deal stage (e.g., 'closedwon', 'closedlost').
        """
        if not self.client: return False
        
        try:
            properties = {
                "dealstage": stage_id
            }
            # Use DealInput wrapper
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

    # --- CLIENT PORTAL FUNCTION ---
    def get_deal_by_email(self, email):
        """
        Wix Portal Logic: Checks active deal by email.
        """
        if not self.client: 
            # Simulation Mode
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
            
            # Extract Drive Link
            drive_link = "https://drive.google.com/" 
            
            return {
                "project": deal.properties['dealname'],
                "status": deal.properties['dealstage'],
                "amount": deal.properties['amount'],
                "link": drive_link
            }
            
        except Exception as e:
            print(f"⚠️ Portal Fetch Error: {e}")
            return None

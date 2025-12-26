import os
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from datetime import datetime

class QuoteGenerator:
    def __init__(self):
        self.output_folder = "generated_quotes"
        os.makedirs(self.output_folder, exist_ok=True)

    def generate_pdf(self, user_name, project_type, estimated_cost, deal_id="000"):
        """
        Generates a Luxury PDF Quote with 'Accept/Reject' links.
        Returns: (filepath, filename)
        Updated to include deal_id in links for tracking.
        """
        # Filename Logic
        clean_name = user_name.replace(' ', '_')
        date_str = datetime.now().strftime('%Y%m%d')
        filename = f"Quote_{clean_name}_{date_str}.pdf"
        filepath = os.path.join(self.output_folder, filename)
        
        c = canvas.Canvas(filepath, pagesize=letter)
        width, height = letter

        # --- 1. HEADER (Branding) ---
        # Gold Color for Luxury Feel (Client requested Gold/Orange/Black)
        c.setFillColorRGB(0.85, 0.65, 0.13) # Gold
        c.rect(0, height - 100, width, 100, fill=True, stroke=False)
        
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 24)
        c.drawString(50, height - 60, "F&L DESIGN BUILDERS")
        
        c.setFont("Helvetica", 12)
        c.drawString(50, height - 80, "Luxury Design & Construction | Woman-Owned")
        
        # --- 2. CLIENT DETAILS ---
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, height - 150, f"Prepared For: {user_name}")
        c.setFont("Helvetica", 12)
        c.drawString(50, height - 170, f"Project: {project_type}")
        c.drawString(50, height - 190, f"Date: {datetime.now().strftime('%B %d, %Y')}")

        # --- 3. THE ESTIMATE (Game Changer Logic) ---
        c.setLineWidth(1)
        c.line(50, height - 220, 550, height - 220)
        
        c.setFont("Helvetica-Bold", 18)
        c.drawString(50, height - 260, "Estimated Investment")
        
        c.setFont("Helvetica-Bold", 30)
        c.setFillColorRGB(0.85, 0.65, 0.13) # Gold Price
        c.drawString(50, height - 300, f"${estimated_cost}")
        
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 10)
        c.drawString(50, height - 320, "*Includes initial design, labor, and standard materials.")

        # --- 4. BUSINESS RULES (From Chat & Files) ---
        c.setFont("Helvetica-Oblique", 12)
        c.setFillColor(colors.darkblue)
        # Requirement: 8-Months Financing (Hardcoded Override)
        c.drawString(50, height - 360, "Payment Option: 8-Months Same-As-Cash Financing Available.")
        
        c.setFillColor(colors.black)
        # Requirement: Venicasa Partnership
        c.drawString(50, height - 380, "Exclusive: Includes complimentary Venicasa Furniture Consultation.")
        
        # --- 5. CALL TO ACTION (Clickable Links) ---
        # Currently localhost for testing, will be replaced by live domain
        api_base = os.getenv("API_BASE_URL", "http://localhost:8000")
        
        # KEY UPDATE: Using deal_id instead of user name for tracking
        accept_link = f"{api_base}/quote/accept?deal_id={deal_id}"
        reject_link = f"{api_base}/quote/reject?deal_id={deal_id}"

        # Accept Button
        c.setFillColorRGB(0.2, 0.6, 0.2) # Green
        c.rect(50, height - 500, 200, 40, fill=True, stroke=False)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(85, height - 475, "ACCEPT QUOTE")
        c.linkURL(accept_link, (50, height - 500, 250, height - 460))

        # Reject Button
        c.setFillColorRGB(0.8, 0.2, 0.2) # Red
        c.rect(300, height - 500, 200, 40, fill=True, stroke=False)
        c.setFillColor(colors.white)
        c.drawString(335, height - 475, "REJECT / FEEDBACK")
        c.linkURL(reject_link, (300, height - 500, 500, height - 460))

        # Footer
        c.setFillColor(colors.gray)
        c.setFont("Helvetica", 9)
        c.drawString(50, 50, "F&L Design Builders | 7315 Wisconsin Avenue, Bethesda, MD | (202) 361-3592")
        
        c.save()
        
        # Returning both path and filename

        return filepath, filename


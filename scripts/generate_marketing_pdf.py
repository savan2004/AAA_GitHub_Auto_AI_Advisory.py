"""
scripts/generate_marketing_pdf.py

Generate a 1-page printable marketing PDF for the Auto Advisory product.

Usage:
  - Install dependencies: pip install reportlab
  - Run: python scripts/generate_marketing_pdf.py

This script creates: output/marketing/Auto_Advisory_Marketing_{YYYYMMDD_HHMMSS}_IST.pdf

Design notes:
  - A4 page, one-sided
  - Brand palette: navy, green, accent, border
  - Header banner, title, subtitle, bullets, CTA, contact, footer with IST timestamp
"""

import os
from datetime import datetime, timedelta
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# Palette
NAVY_HEX = "#0B2545"
GREEN_HEX = "#1B8A5A"
ACCENT_HEX = "#1E88E5"
BORDER_HEX = "#D6DBE3"

NAVY = colors.HexColor(NAVY_HEX)
GREEN = colors.HexColor(GREEN_HEX)
ACCENT = colors.HexColor(ACCENT_HEX)
BORDER = colors.HexColor(BORDER_HEX)
GREY = colors.HexColor("#5A6472")

OUT_DIR = os.path.join("output", "marketing")
os.makedirs(OUT_DIR, exist_ok=True)

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm


def _ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def _draw_header(c):
    # Top banner
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 48, PAGE_W, 48, fill=1, stroke=0)
    # Title
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(MARGIN, PAGE_H - 34, "Auto Advisory")
    c.setFont("Helvetica", 10)
    c.drawString(MARGIN, PAGE_H - 46, "Automatic Equity Research & PDF Report Generator")
    # Right small badge / CTA
    c.setFillColor(ACCENT)
    c.roundRect(PAGE_W - MARGIN - 120, PAGE_H - 40, 120, 28, 6, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(PAGE_W - MARGIN - 60, PAGE_H - 22, "Try: /report SYMBOL")


def _draw_body(c):
    left_x = MARGIN
    y = PAGE_H - 80

    # Short pitch
    c.setFillColor(GREY)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(left_x, y, "Instant, professional equity research — in one PDF")
    y -= 14 + 8

    # Two-column features
    c.setFont("Helvetica", 10)
    col_w = (PAGE_W - 2 * MARGIN - 12) / 2

    features = [
        ("Comprehensive", "Technical indicators (RSI, MACD, EMA/SMA, ADX, ATR, Bollinger, ASI)"),
        ("Fundamentals", "PE, Forward PE, Revenue (TTM), ROE, EPS, Debt/Equity, Dividend Yield"),
        ("AI Outlook", "Concise AI-generated market outlook reusing the same data") ,
        ("News Digest", "Recent curated news lines for quick context"),
        ("Charts", "6-month price chart with moving averages and volume") ,
        ("IST Timestamps", "Filename and footer timestamps use India Standard Time (UTC+5:30)"),
    ]

    bullet_x = left_x + 6
    col1_y = y
    col2_y = y

    # Draw feature bullets
    for i, (h, t) in enumerate(features):
        if i < 3:
            tx = left_x
            ty = col1_y - i * 36
        else:
            tx = left_x + col_w + 12
            ty = col2_y - (i - 3) * 36

        # icon circle
        c.setFillColor(GREEN)
        c.circle(tx + 6, ty + 6, 5, fill=1, stroke=0)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(tx + 16, ty + 2, h)
        # description
        para_style = ParagraphStyle('feat', fontName='Helvetica', fontSize=9, leading=12)
        p = Paragraph(t, para_style)
        w, h_used = p.wrap(col_w - 24, 36)
        p.drawOn(c, tx + 16, ty - 12)

    # CTA block
    c.setFillColor(NAVY)
    c.roundRect(left_x, PAGE_H/2 - 30, PAGE_W - 2 * MARGIN, 60, 6, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(left_x + 12, PAGE_H/2 - 8, "Get professional PDF reports in seconds — directly in Telegram")
    c.setFont("Helvetica", 11)
    c.drawString(left_x + 12, PAGE_H/2 - 26, "Send: /report <SYMBOL>  (e.g., /report TCS)")

    # Small note and contact
    c.setFillColor(GREY)
    c.setFont("Helvetica", 9)
    note_y = PAGE_H/2 - 56
    c.drawString(left_x, note_y, "Use cases: fast pre-trade checks, client-ready reports, watchlist snapshots, scheduled newsletters.")
    c.drawString(left_x, note_y - 14, "Integration: Telegram bot, webhook, or embed in your workflows. Repo: github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py")


def _draw_footer(c, generated_at_str):
    # Divider
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.line(MARGIN, 60, PAGE_W - MARGIN, 60)

    # Disclaimer center
    c.setFillColor(GREY)
    c.setFont("Helvetica", 8)
    disclaimer = "Educational use only. Not investment advice. Always consult a qualified financial advisor before making investment decisions."
    c.drawCentredString(PAGE_W/2, 48, disclaimer)

    # Left timestamp
    c.setFont("Helvetica", 8)
    c.drawString(MARGIN, 36, f"Generated: {generated_at_str}")

    # Right contact
    c.drawRightString(PAGE_W - MARGIN, 36, "Contact: github.com/savan2004")


def generate_marketing_pdf():
    ist = _ist_now()
    generated_at = ist.strftime("%d-%b-%Y %H:%M IST")
    filename_ts = ist.strftime("%Y%m%d_%H%M%S")
    out_name = f"Auto_Advisory_Marketing_{filename_ts}_IST.pdf"
    out_path = os.path.join(OUT_DIR, out_name)

    c = canvas.Canvas(out_path, pagesize=A4)

    _draw_header(c)
    _draw_body(c)
    _draw_footer(c, generated_at)

    c.showPage()
    c.save()

    print("Marketing PDF generated:", out_path)


if __name__ == '__main__':
    generate_marketing_pdf()

"""
Branded HTML emails for Phenyo Move & Meal.

Email programs (Outlook, Gmail, phone apps) understand only old-fashioned HTML:
tables for layout and styles written inline on every element. Modern CSS is
ignored or stripped, so this file looks dated on purpose - it is what renders
the same everywhere.

The logo is attached to the email itself ("inline" attachment, referenced as
cid:logo) rather than linked from the website. Many email programs block
images from the internet until the reader clicks "show images"; an attached
image shows straight away.
"""

import base64
from html import escape
from pathlib import Path

# Brand colours, taken from the website's design system (src/index.css).
DARK = "#1b3226"  # deep green - header and footer
GREEN = "#1d8749"  # primary green - buttons and accents
GOLD = "#ec9213"  # accent - the slogan line
TEXT = "#24312a"
MUTED = "#5f6f66"
PAGE = "#f3f6f4"

COMPANY = "Phenyo Move & Meal"
SLOGAN = "Every move is a win"
PHONE = "+27 72 182 9894"
WHATSAPP_URL = "https://wa.me/27721829894"
WEBSITE = "https://www.movenmeal.com"
SIGNATURE_NAME = "Moshe Sekhula"
SIGNATURE_TITLE = "Founder"

LOGO_PATH = Path(__file__).parent / "static" / "email-logo.png"


def logo_attachment() -> dict:
    """The logo as a Graph inline attachment, shown wherever the HTML says cid:logo."""
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": "phenyo-move-and-meal.png",
        "contentType": "image/png",
        "contentId": "logo",
        "isInline": True,
        "contentBytes": base64.b64encode(LOGO_PATH.read_bytes()).decode(),
    }


def button(label: str, url: str, colour: str = GREEN) -> str:
    return (
        f'<a href="{escape(url)}" style="display:inline-block;background:{colour};'
        "color:#ffffff;text-decoration:none;font-weight:bold;font-size:15px;"
        f'padding:12px 22px;border-radius:6px;margin:4px 6px 4px 0">{escape(label)}</a>'
    )


def details_table(rows: list[tuple[str, str]]) -> str:
    """Two-column summary. Values are escaped: they come from a public form."""
    cells = "".join(
        f'<tr><td style="padding:8px 12px;color:{MUTED};font-size:14px;width:38%;'
        f'border-bottom:1px solid #e6ece8;vertical-align:top">{escape(label)}</td>'
        f'<td style="padding:8px 12px;color:{TEXT};font-size:14px;font-weight:bold;'
        f'border-bottom:1px solid #e6ece8">{escape(value)}</td></tr>'
        for label, value in rows
        if value
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;background:#f8faf9;border-radius:8px;'
        f'margin:18px 0">{cells}</table>'
    )


def layout(preheader: str, content: str, signature: bool = True) -> str:
    """The frame every email shares: logo header, slogan band, content, signature, footer."""
    signature_block = f"""<tr><td style="padding:8px 32px 32px">
    <table role="presentation" cellpadding="0" cellspacing="0"
     style="border-top:3px solid {GOLD};width:100%">
      <tr><td style="padding-top:16px;color:{TEXT};font-size:14px;line-height:1.5">
        <em style="color:{GOLD};font-size:15px;font-weight:bold">{SLOGAN}.</em><br><br>
        <strong style="font-size:15px">{SIGNATURE_NAME}</strong><br>
        <span style="color:{MUTED}">{SIGNATURE_TITLE}, {COMPANY}</span><br>
        <a href="tel:{PHONE.replace(" ", "")}" style="color:{GREEN};text-decoration:none">{PHONE}</a>
        &nbsp;|&nbsp;
        <a href="{WHATSAPP_URL}" style="color:{GREEN};text-decoration:none">WhatsApp</a>
        &nbsp;|&nbsp;
        <a href="{WEBSITE}" style="color:{GREEN};text-decoration:none">movenmeal.com</a>
      </td></tr>
    </table>
  </td></tr>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{COMPANY}</title>
</head>
<body style="margin:0;padding:0;background:{PAGE};font-family:Arial,Helvetica,sans-serif">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE}">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
 style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden">

  <tr><td align="center" style="background:{DARK};padding:28px 24px 22px">
    <img src="cid:logo" width="220" alt="{COMPANY}"
     style="display:block;width:220px;max-width:70%;height:auto;border:0">
  </td></tr>
  <tr><td align="center" style="background:{GREEN};padding:9px 24px;color:#ffffff;
   font-size:13px;letter-spacing:2px;text-transform:uppercase;font-weight:bold">
    Safe &middot; Reliable &middot; On time
  </td></tr>

  <tr><td style="padding:32px 32px 8px;color:{TEXT};font-size:15px;line-height:1.6">
    {content}
  </td></tr>

  {signature_block if signature else '<tr><td style="padding:0 32px 24px"></td></tr>'}

  <tr><td align="center" style="background:{DARK};padding:18px 24px;color:#b9c8bf;

   font-size:12px;line-height:1.6">
    Airport transfers &middot; Corporate &amp; staff shuttles &middot; Group transport &middot; Tours<br>
    Johannesburg, South Africa
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def customer_confirmation(
    first_name: str, reference: int, service: str, rows: list[tuple[str, str]]
) -> str:
    content = f"""
    <h1 style="margin:0 0 6px;font-size:22px;color:{DARK}">Your request is with us</h1>
    <p style="margin:0 0 16px;color:{MUTED};font-size:14px">Reference #{reference}</p>
    <p style="margin:0 0 12px">Hi {escape(first_name)},</p>
    <p style="margin:0 0 12px">Thank you for choosing {COMPANY}. We've received your
    request for <strong>{escape(service)}</strong> and our team is already on it.</p>
    {details_table(rows)}
    <p style="margin:0 0 6px"><strong>What happens next?</strong></p>
    <p style="margin:0 0 18px">We'll send you a tailored quote <strong>within 24 hours</strong>.
    Need to change something or add a stop? Simply reply to this email or message us
    on WhatsApp - quote your reference #{reference}.</p>
    <p style="margin:0 0 24px">
      {button("Chat on WhatsApp", WHATSAPP_URL, "#25D366")}
      {button("Visit our website", WEBSITE)}
    </p>
    <p style="margin:0">We look forward to moving you.</p>
    """
    return layout(
        f"We've received your request #{reference}. Your quote follows within 24 hours.", content
    )


def sales_notification(
    reference: int,
    service: str,
    contact_name: str,
    phone: str,
    rows: list[tuple[str, str]],
    customer_emailed: bool,
) -> str:
    digits = phone.lstrip("+")
    whatsapp = "https://wa.me/" + ("27" + digits[1:] if digits.startswith("0") else digits)
    confirmation = (
        "They've been emailed a confirmation promising a quote within 24 hours."
        if customer_emailed
        else "They gave no email address, so contact them by phone or WhatsApp."
    )
    content = f"""
    <h1 style="margin:0 0 6px;font-size:22px;color:{DARK}">New quote request #{reference}</h1>
    <p style="margin:0 0 16px;color:{MUTED};font-size:14px">{escape(service.capitalize())}
    &middot; from the website</p>
    <p style="margin:0 0 4px"><strong>{escape(contact_name)}</strong> is waiting for a quote.
    {confirmation}</p>
    {details_table(rows)}
    <p style="margin:0 0 24px">
      {button("Call " + phone, "tel:" + phone)}
      {button("WhatsApp customer", whatsapp, "#25D366")}
    </p>
    <p style="margin:0;color:{MUTED};font-size:13px">Pressing Reply writes straight to the
    customer (when they gave an email address). Once you've sent the price, mark request
    #{reference} as quoted in /docs.</p>
    """
    return layout(
        f"New request #{reference}: {service} for {contact_name}", content, signature=False
    )

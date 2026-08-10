import html as _html
import logging
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Dict, Any

from app.config import settings

logger = logging.getLogger(__name__)

# Base URL for links. Override via BASE_URL env var in production.
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")


def _narrative_to_html(narrative: str) -> str:
    """
    Convert the AIDA narrative (Markdown) to clean, email-safe HTML.
    Supports: **bold**, ## and ### headings, paragraphs, inline links.
    """
    if not narrative:
        return "<p>We have some personalized picks waiting for you on the dashboard.</p>"

    # 1. Escape HTML first (injection safety)
    text = _html.escape(narrative)

    # 2. Headings
    text = re.sub(r"^###\s*(.+)$", r'<h3 style="margin:22px 0 8px; font-size:15px; color:#0f172a; font-weight:700;">\1</h3>', text, flags=re.M)
    text = re.sub(r"^##\s*(.+)$",  r'<h2 style="margin:26px 0 10px; font-size:17px; color:#0f172a; font-weight:700;">\1</h2>', text, flags=re.M)

    # 3. Bold
    text = re.sub(r"\*\*(.+?)\*\*", r'<strong style="color:#0f172a;">\1</strong>', text)

    # 4. Split on double newlines → paragraph blocks
    blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]
    out = []
    for b in blocks:
        if b.startswith("<h2") or b.startswith("<h3"):
            out.append(b)
        else:
            # Single newlines become <br> inside the paragraph
            paragraph = b.replace("\n", "<br>")
            out.append(
                f'<p style="margin:0 0 14px; color:#334155; font-size:15px; '
                f'line-height:1.7; font-family: -apple-system, BlinkMacSystemFont, '
                f'\'Segoe UI\', Roboto, sans-serif;">{paragraph}</p>'
            )
    return "".join(out)


def _price_text(price: Any) -> str:
    try:
        p = float(price)
        return "Free" if p <= 0 else f"${p:,.0f}"
    except (TypeError, ValueError):
        return "—"


def _build_html(user_name: str, narrative_html: str, courses: List[Dict[str, Any]]) -> str:
    """Compose the full beautiful email HTML."""

    # Course cards HTML
    course_cards = ""
    if courses:
        cards = []
        for c in courses[:5]:  # cap at 5 so the email stays readable
            title = _html.escape(c.get("title") or "Course")
            category = _html.escape(c.get("category") or "")
            level = _html.escape(c.get("level") or "")
            price = _price_text(c.get("price"))
            course_url = f"{BASE_URL}/course/{c.get('id')}"

            badges = "".join(
                f'<span style="display:inline-block; padding:3px 10px; border-radius:999px; '
                f'background:#e0f2fe; color:#0369a1; font-size:11px; font-weight:600; '
                f'letter-spacing:0.02em; margin-right:6px; margin-bottom:4px;">{_html.escape(b)}</span>'
                for b in [category, level, price] if b
            )

            cards.append(f"""
                <a href="{course_url}" style="text-decoration:none; color:inherit; display:block;
                   padding:18px 20px; margin:12px 0; background:#f8fafc; border:1px solid #e2e8f0;
                   border-radius:12px; transition:all 0.15s; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                    <div style="font-size:15px; font-weight:700; color:#0f172a; line-height:1.4; margin-bottom:8px;">
                        {title}
                    </div>
                    <div style="margin-bottom:10px;">{badges}</div>
                    <div style="font-size:13px; color:#0369a1; font-weight:600;">
                        View course →
                    </div>
                </a>
            """)
        course_cards = "".join(cards)

    dashboard_url = f"{BASE_URL}/dashboard"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Your AURA Daily Digest</title>
</head>
<body style="margin:0; padding:0; background:#f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9; padding:32px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px; width:100%;">

          <!-- HEADER -->
          <tr>
            <td style="padding:24px 0 8px; text-align:center;">
              <div style="font-size:28px; font-weight:700; letter-spacing:-0.02em; color:#0f172a; font-family: 'Georgia', serif;">
                AURA<span style="font-size:11px; vertical-align:super; color:#64748b; margin-left:4px;">TM</span>
              </div>
              <div style="font-size:11px; letter-spacing:0.18em; color:#64748b; text-transform:uppercase; margin-top:6px;">
                Daily Personalized Digest
              </div>
            </td>
          </tr>

          <!-- CARD -->
          <tr>
            <td style="background:#ffffff; border-radius:16px; padding:40px 36px; box-shadow:0 4px 24px rgba(15, 23, 42, 0.06); border:1px solid #e2e8f0;">

              <!-- Greeting -->
              <div style="font-size:14px; color:#64748b; margin-bottom:8px;">
                Good {_greeting()}, {_html.escape(user_name)} —
              </div>
              <h1 style="margin:0 0 20px; font-size:26px; color:#0f172a; line-height:1.3; font-weight:700; letter-spacing:-0.01em;">
                Today's learning, chosen for you.
              </h1>

              <!-- Gradient divider -->
              <div style="height:3px; width:64px; border-radius:999px; background:linear-gradient(90deg, #14b8a6 0%, #8b5cf6 50%, #f59e0b 100%); margin-bottom:28px;"></div>

              <!-- Dashboard CTA -->
              <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 28px;">
                <tr>
                  <td style="border-radius:999px; background:linear-gradient(135deg, #14b8a6 0%, #8b5cf6 50%, #f59e0b 100%);">
                    <a href="{dashboard_url}" style="display:inline-block; padding:12px 24px; font-size:14px; font-weight:700; color:#ffffff; text-decoration:none; letter-spacing:0.01em;">
                      Open your dashboard →
                    </a>
                  </td>
                </tr>
              </table>

              <!-- Narrative -->
              <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                {narrative_html}
              </div>

              <!-- Course cards -->
              {f'<h3 style="margin:32px 0 4px; font-size:14px; color:#64748b; font-weight:600; text-transform:uppercase; letter-spacing:0.08em;">Your picks for today</h3>' if courses else ''}
              {course_cards}

              <!-- Secondary link -->
              <div style="margin-top:28px; padding-top:24px; border-top:1px solid #e2e8f0; text-align:center;">
                <a href="{BASE_URL}/catalog" style="font-size:14px; color:#0369a1; text-decoration:none; font-weight:600;">
                  Browse the full catalog →
                </a>
              </div>
            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td style="padding:24px 12px 0; text-align:center; color:#94a3b8; font-size:12px; line-height:1.6;">
              This digest is sent once per day based on your on-platform activity.
              <br />
              AURA SmartReco · Agentic learning, explained.
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "morning"
    elif hour < 17:
        return "afternoon"
    return "evening"


def _build_plain_text(user_name: str, narrative: str, courses: List[Dict[str, Any]]) -> str:
    """Plain-text fallback for email clients that strip HTML."""
    lines = [
        f"Hi {_html.escape(user_name)},",
        "",
        "Here's your AURA daily digest:",
        "",
        narrative or "Personalized picks are waiting on your dashboard.",
        "",
    ]
    if courses:
        lines.append("Your picks for today:")
        for i, c in enumerate(courses[:5], 1):
            title = c.get("title") or "Course"
            price = _price_text(c.get("price"))
            lines.append(f"  {i}. {title} — {price}")
            lines.append(f"     {BASE_URL}/course/{c.get('id')}")
        lines.append("")
    lines.append(f"Open your dashboard: {BASE_URL}/dashboard")
    lines.append(f"Browse catalog:      {BASE_URL}/catalog")
    return "\n".join(lines)


def send_daily_digest_email(
    user_email: str,
    user_name: str,
    narrative: str,
    courses: List[Dict[str, Any]],
) -> bool:
    """
    Build and send a beautiful HTML digest email via Gmail SMTP (port 465 SSL).
    Falls back to logging-only if SMTP is not configured.
    """
    smtp_host = getattr(settings, "SMTP_HOST", None) or os.getenv("SMTP_HOST")
    smtp_port = int(getattr(settings, "SMTP_PORT", 465) or os.getenv("SMTP_PORT") or 465)
    smtp_user = getattr(settings, "SMTP_USER", None) or os.getenv("SMTP_USER")
    smtp_password = (getattr(settings, "SMTP_PASSWORD", None) or os.getenv("SMTP_PASSWORD") or "").replace(" ", "")
    mail_from = (
        getattr(settings, "MAIL_FROM", None)
        or os.getenv("MAIL_FROM")
        or (smtp_user if smtp_user else "no-reply@aura.smartreco.ai")
    )

    narrative_html = _narrative_to_html(narrative)
    html_body = _build_html(user_name, narrative_html, courses)
    text_body = _build_plain_text(user_name, narrative, courses)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🚀 Your Daily Personalized Course Digest — AURA"
    msg["From"] = f"AURA SmartReco <{mail_from}>" if "<" not in mail_from else mail_from
    msg["To"] = user_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if not smtp_host:
        logger.warning(
            f"[EmailService] SMTP not configured. Would have sent digest to {user_email}. "
            f"Set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/MAIL_FROM in .env"
        )
        return False

    logger.info(f"[EmailService] Attempting SMTP connection to {smtp_host}:{smtp_port}")
    logger.info(f"[EmailService] Using user: {smtp_user}")
    logger.info(f"[EmailService] Password length: {len(smtp_password)} chars")

    try:
        # Use SMTP_SSL for port 465 (direct SSL, no STARTTLS)
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            if smtp_user and smtp_password:
                logger.info(f"[EmailService] Authenticating as {smtp_user}...")
                server.login(smtp_user, smtp_password)
                logger.info(f"[EmailService] Authentication successful")
            server.sendmail(mail_from, [user_email], msg.as_string())
        logger.info(f"[EmailService] Digest delivered to {user_email}")
        return True
    except smtplib.SMTPAuthenticationError as auth_err:
        logger.error(f"[EmailService] SMTP Authentication failed: {auth_err}")
        logger.error(f"[EmailService] This usually means: (1) wrong app password, (2) 2FA not enabled, or (3) Less Secure Apps disabled")
        return False
    except Exception as e:
        logger.error(f"[EmailService] Failed to send to {user_email}: {e}")
        return False


def send_password_reset_email(user_email: str, reset_token: str) -> bool:
    """Send a one-click password reset email with a 15-minute link."""
    smtp_host = getattr(settings, "SMTP_HOST", None) or os.getenv("SMTP_HOST")
    smtp_port = int(getattr(settings, "SMTP_PORT", 465) or os.getenv("SMTP_PORT") or 465)
    smtp_user = getattr(settings, "SMTP_USER", None) or os.getenv("SMTP_USER")
    smtp_password = (getattr(settings, "SMTP_PASSWORD", None) or os.getenv("SMTP_PASSWORD") or "").replace(" ", "")
    mail_from = (
        getattr(settings, "MAIL_FROM", None)
        or os.getenv("MAIL_FROM")
        or (smtp_user if smtp_user else "no-reply@aura.smartreco.ai")
    )

    reset_url = f"{BASE_URL}/auth/reset-page?token={reset_token}"

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8" /><title>Reset your AURA password</title></head>
<body style="margin:0; padding:0; background:#f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9; padding:32px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px; width:100%;">
          <tr>
            <td style="padding:24px 0 8px; text-align:center;">
              <div style="font-size:28px; font-weight:700; letter-spacing:-0.02em; color:#0f172a; font-family: 'Georgia', serif;">
                AURA<span style="font-size:11px; vertical-align:super; color:#64748b; margin-left:4px;">TM</span>
              </div>
            </td>
          </tr>
          <tr>
            <td style="background:#ffffff; border-radius:16px; padding:40px 36px; box-shadow:0 4px 24px rgba(15, 23, 42, 0.06); border:1px solid #e2e8f0;">
              <h1 style="margin:0 0 20px; font-size:22px; color:#0f172a; line-height:1.3; font-weight:700;">
                Reset your password
              </h1>
              <div style="height:3px; width:64px; border-radius:999px; background:linear-gradient(90deg, #14b8a6 0%, #8b5cf6 50%, #f59e0b 100%); margin-bottom:24px;"></div>
              <p style="margin:0 0 20px; color:#334155; font-size:15px; line-height:1.7;">
                We received a request to reset the password for your AURA account.
                Click the button below to choose a new password. This link expires in <strong>15 minutes</strong>.
              </p>
              <table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0;">
                <tr>
                  <td style="border-radius:999px; background:linear-gradient(135deg, #14b8a6 0%, #8b5cf6 50%, #f59e0b 100%);">
                    <a href="{reset_url}" style="display:inline-block; padding:12px 28px; font-size:14px; font-weight:700; color:#ffffff; text-decoration:none;">
                      Reset my password →
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:28px 0 0; font-size:13px; color:#64748b; line-height:1.6;">
                If you didn't request this, you can safely ignore this email. Your password hasn't changed.
              </p>
              <p style="margin:12px 0 0; font-size:12px; color:#94a3b8; line-height:1.6;">
                If the button doesn't work, copy this link:<br />
                <a href="{reset_url}" style="color:#0369a1; word-break:break-all;">{reset_url}</a>
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 12px 0; text-align:center; color:#94a3b8; font-size:12px; line-height:1.6;">
              AURA SmartReco · Agentic learning, explained.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    text_body = (
        f"Reset your AURA password\n\n"
        f"Click this link within 15 minutes:\n{reset_url}\n\n"
        f"If you didn't request this, ignore this email."
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🔐 Reset your AURA password"
    msg["From"] = f"AURA SmartReco <{mail_from}>" if "<" not in mail_from else mail_from
    msg["To"] = user_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if not smtp_host:
        logger.warning(f"[EmailService] SMTP not configured; would have sent reset link to {user_email}")
        return False

    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(mail_from, [user_email], msg.as_string())
        logger.info(f"[EmailService] Password reset email delivered to {user_email}")
        return True
    except Exception as e:
        logger.error(f"[EmailService] Failed to send reset email to {user_email}: {e}")
        return False
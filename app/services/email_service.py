import logging
from typing import List, Dict, Any
from app.config import settings

logger = logging.getLogger(__name__)


def send_daily_digest_email(
    user_email: str,
    user_name: str,
    narrative: str,
    courses: List[Dict[str, Any]]
) -> bool:
    """
    Render and send daily digest recommendation email.
    If SMTP credentials are not configured, gracefully log output as fallback.
    """
    try:
        logger.info(f"Rendering Daily Digest Email for {user_email}...")
        
        # Check if SMTP configured
        if not settings.SMTP_HOST or not settings.SMTP_USER:
            logger.info(f"[Email Service Fallback] SMTP credentials unconfigured. Email digest logged to console for {user_email}:")
            logger.info(f"Subject: 🚀 Your Daily Personalized Course Digest - SmartReco 2026")
            logger.info(f"To: {user_email}")
            logger.info(f"Body snippet:\n{narrative[:300]}...")
            return True

        # In production with SMTP configured:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🚀 Your Daily Personalized Course Digest - SmartReco 2026"
        msg["From"] = settings.SMTP_USER
        msg["To"] = user_email

        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #0f172a; color: #f8fafc; padding: 20px;">
                <div style="max-width: 600px; margin: 0 auto; background-color: #1e293b; padding: 24px; border-radius: 12px;">
                    <h2 style="color: #38bdf8;">Hello {user_name or 'Learner'},</h2>
                    <p style="font-size: 15px; line-height: 1.6;">Here is your personalized AI recommendation digest for today:</p>
                    <div style="background-color: #0f172a; padding: 16px; border-left: 4px solid #38bdf8; border-radius: 6px; margin: 20px 0;">
                        {narrative}
                    </div>
                    <p style="color: #94a3b8; font-size: 12px; margin-top: 30px;">SmartReco 2026 — Powered by LangGraph & Mesh API</p>
                </div>
            </body>
        </html>
        """
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.sendmail(settings.SMTP_USER, user_email, msg.as_string())

        logger.info(f"Successfully sent daily digest email to {user_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {user_email}: {str(e)}")
        return False

import sys
import getpass
import logging
from sqlalchemy.orm import Session
from app.core.database import SessionLocal, Base, engine
from app.models.user import User
from app.core.security import get_password_hash

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_admin(email: str = None, password: str = None, full_name: str = "Admin User"):
    """Create a new admin user in the system."""
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()

    try:
        if not email:
            email = input("Enter admin email: ").strip()
        if not password:
            password = getpass.getpass("Enter admin password: ").strip()

        if not email or not password:
            logger.error("Email and password cannot be empty.")
            sys.exit(1)

        existing = db.query(User).filter(User.email == email).first()
        if existing:
            if not existing.is_admin:
                existing.is_admin = True
                db.commit()
                logger.info(f"User '{email}' updated to admin.")
            else:
                logger.info(f"Admin user '{email}' already exists.")
            return

        admin_user = User(
            email=email,
            full_name=full_name,
            hashed_password=get_password_hash(password),
            is_admin=True
        )
        db.add(admin_user)
        db.commit()
        logger.info(f"Successfully created admin user: {email}")

    except Exception as e:
        logger.error(f"Failed to create admin user: {str(e)}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    email_arg = sys.argv[1] if len(sys.argv) > 1 else None
    pass_arg = sys.argv[2] if len(sys.argv) > 2 else None
    create_admin(email_arg, pass_arg)

import asyncio
import logging
import app._grpc_fix
from app.config import settings
from app.core.database import SessionLocal
from app.services.recommendation_service import RecommendationService
from app.core.llm import generate_chat_completion

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    db = SessionLocal()
    try:
        logger.info(f"=== Testing Mesh API Chat Completion with model '{settings.DEFAULT_CHAT_MODEL}' ===")
        res = generate_chat_completion(
            model=settings.DEFAULT_CHAT_MODEL,
            messages=[{"role": "user", "content": "Hello, confirm you are connected via Mesh API."}],
            temperature=0.7
        )
        logger.info(f"Mesh API Response: {res}")

        logger.info("=== Testing LangGraph Recommendation Agent ===")
        rec = await RecommendationService.generate_and_store(
            db=db,
            user_id=2,
            trigger_reason="live_manual_test"
        )
        logger.info(f"Generated Recommendation ID: {rec.get('id')}")
        logger.info(f"Quality Score: {rec.get('quality_score')}")
        logger.info(f"Product IDs: {rec.get('product_ids')}")
        logger.info(f"Narrative Snippet:\n{rec.get('narrative')[:300]}...")

        logger.info("=== ALL SYSTEM TESTS PASSED SUCCESSFULLY ===")
    except Exception as e:
        logger.error(f"Test failed with error: {str(e)}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

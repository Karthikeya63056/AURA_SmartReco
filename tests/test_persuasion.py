import pytest
from unittest.mock import MagicMock, patch
from app.agent.nodes import _infer_persuasion_style, generate_narrative_node
from app.agent.prompts import (
    PERSUASIVE_PROMPT_ANALYTICAL,
    PERSUASIVE_PROMPT_SOCIAL,
    PERSUASIVE_PROMPT_MOTIVATIONAL,
    PERSUASIVE_PROMPT_PRACTICAL,
    PERSUASIVE_PROMPT_HYBRID,
)


def test_infer_persuasion_style_analytical():
    """Test analytical keywords produce 'analytical' style."""
    events_summary = "Searched for 'architecture and benchmark performance'. Viewed syllabus details."
    user_profile = {"skill_level": "Advanced", "intent": "Upskilling"}
    assert _infer_persuasion_style(events_summary, user_profile) == "analytical"


def test_infer_persuasion_style_social():
    """Test social keywords produce 'social' style."""
    events_summary = "Searched for 'community cohort reviews'. Viewed popular student ratings."
    user_profile = {"skill_level": "Beginner", "intent": "Explore"}
    assert _infer_persuasion_style(events_summary, user_profile) == "social"


def test_infer_persuasion_style_motivational():
    """Test motivational keywords produce 'motivational' style."""
    events_summary = "Searched for 'career growth and challenge roadmap'. Viewed master transformation."
    user_profile = {"skill_level": "Intermediate", "intent": "Career Change"}
    assert _infer_persuasion_style(events_summary, user_profile) == "motivational"


def test_infer_persuasion_style_practical():
    """Test practical keywords produce 'practical' style."""
    events_summary = "Searched for 'hands-on portfolio project job implementation'."
    user_profile = {"skill_level": "Intermediate", "intent": "Upskilling"}
    assert _infer_persuasion_style(events_summary, user_profile) == "practical"


def test_infer_persuasion_style_hybrid_fallback():
    """Test empty/no signals produce 'hybrid' style."""
    events_summary = "No recent events."
    user_profile = {"skill_level": "Beginner", "intent": "General"}
    assert _infer_persuasion_style(events_summary, user_profile) == "hybrid"


def test_generate_narrative_selects_correct_prompt_variant():
    """Test generate_narrative_node selects the appropriate prompt based on state persuasion_style."""
    state = {
        "user_profile": {"interests": ["AI"], "skill_level": "Intermediate", "intent": "Upskilling"},
        "user_skills": ["Python Basics"],
        "recommended_product_ids": [1],
        "persuasion_style": "analytical",
        "critique_feedback": ""
    }

    mock_product = MagicMock()
    mock_product.title = "AI Architecture"
    mock_product.level = "Intermediate"
    mock_product.category = "AI"
    mock_product.description = "Deep dive into performance."
    mock_product.prerequisites = ["Python Basics"]
    mock_product.skills_taught = ["Architecture"]

    with patch("app.agent.nodes.SessionLocal") as mock_session_cls, \
         patch("app.agent.nodes.get_product", return_value=mock_product), \
         patch("app.agent.nodes.generate_chat_completion", return_value="Generated analytical narrative content between 120 and 280 words long.") as mock_llm:
        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db

        import asyncio
        result = asyncio.run(generate_narrative_node(state))

        assert result["final_narrative"] != ""
        # Check that the prompt passed to generate_chat_completion contained Analytical style cues
        call_args = mock_llm.call_args[1]
        prompt_content = call_args["messages"][0]["content"]
        assert "Analytical Style" in prompt_content
        assert "ROI" in prompt_content

import pytest
from unittest.mock import MagicMock, patch
from app.agent.nodes import _infer_user_skills, retrieve_candidates_node
from app.agent.prompts import PERSUASIVE_PROMPT


def test_infer_user_skills_from_profile_and_events():
    """Test _infer_user_skills infers baseline and topic skills from user profile & events summary."""
    user_profile = {"interests": ["AI & Agents", "Python"], "skill_level": "Intermediate"}
    events_summary = "Searched for 'RAG' 3 times. Viewed 'LangGraph' category."

    skills = _infer_user_skills(events_summary, user_profile)
    
    assert "Python Basics" in skills
    assert "Vector Search" in skills
    assert "RAG" in skills
    assert "Prompt Engineering" in skills


def test_retrieve_candidates_prerequisite_distance_penalty():
    """Test retrieve_candidates_node applies distance penalty to candidates with unmet prerequisites."""
    mock_candidates = [
        {"id": 1, "document": "Python course", "distance": 0.2, "metadata": {"title": "Python Intro"}},
        {"id": 2, "document": "Advanced DL", "distance": 0.2, "metadata": {"title": "Advanced DL"}},
    ]

    p1 = MagicMock()
    p1.id = 1
    p1.title = "Python Intro"
    p1.prerequisites = []
    p1.skills_taught = ["Python Basics"]

    p2 = MagicMock()
    p2.id = 2
    p2.title = "Advanced DL"
    p2.prerequisites = ["PyTorch", "Deep Learning"]
    p2.skills_taught = ["Advanced DL"]

    state = {
        "search_query": "AI courses",
        "refetch_count": 0,
        "user_profile": {"interests": ["AI"], "skill_level": "Beginner"},
        "user_skills": ["Python Basics"],
        "drop_filters": False
    }

    with patch("app.agent.nodes.search_products_vector", return_value=mock_candidates), \
         patch("app.agent.nodes.SessionLocal") as mock_session_cls:
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [p1, p2]
        mock_session_cls.return_value = mock_db

        import asyncio
        result = asyncio.run(retrieve_candidates_node(state))
        res_candidates = result["candidates"]

        # Python Intro (0 unmet prereqs) should come first due to lower distance
        assert res_candidates[0]["id"] == 1
        # Advanced DL distance should be penalized because PyTorch & Deep Learning are unmet
        assert res_candidates[1]["id"] == 2
        assert res_candidates[1]["distance"] > 0.2
        assert "PyTorch" in res_candidates[1]["unmet_prerequisites"]


def test_persuasive_prompt_formatting_with_skills():
    """Test PERSUASIVE_PROMPT formats correctly with user_skills and course prerequisite text."""
    formatted = PERSUASIVE_PROMPT.format(
        intent="Upskilling",
        skill_level="Intermediate",
        interests="AI & Agents",
        user_skills="Python Basics, Vector Search",
        recommended_courses_text="- **LangGraph Masterclass** (Advanced | AI): State Machines\n  * Prerequisites: RAG, LLM APIs\n  * Skills You'll Learn: LangGraph"
    )

    assert "Python Basics, Vector Search" in formatted
    assert "LangGraph Masterclass" in formatted
    assert "Prerequisites: RAG, LLM APIs" in formatted

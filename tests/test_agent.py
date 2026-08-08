from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.agent.graph import should_refetch, should_retry_or_store


def test_should_refetch_trigger():
    """Test conditional edge triggers refetch when score < 60 and refetch_count < 2."""
    state_low_score = {
        "quality_score": 45,
        "refetch_count": 0,
        "metadata": {"needs_refetch": True}
    }
    assert should_refetch(state_low_score) == "refetch"

    state_second_refetch = {
        "quality_score": 50,
        "refetch_count": 1,
        "metadata": {"needs_refetch": False}
    }
    assert should_refetch(state_second_refetch) == "refetch"

    state_max_refetches_reached = {
        "quality_score": 40,
        "refetch_count": 2,
        "metadata": {"needs_refetch": True}
    }
    assert should_refetch(state_max_refetches_reached) == "proceed"


def test_should_proceed_when_high_score():
    """Test conditional edge proceeds directly when score >= 60."""
    state_high_score = {
        "quality_score": 85,
        "refetch_count": 0,
        "metadata": {"needs_refetch": False}
    }
    assert should_refetch(state_high_score) == "proceed"


@pytest.mark.asyncio
async def test_rewrite_query_success():
    """Test _rewrite_query returns rewritten query bounded to max 10 words."""
    from app.agent.nodes import _rewrite_query

    with patch(
        "app.agent.nodes.generate_chat_completion",
        new=AsyncMock(return_value='"advanced machine learning neural networks deep learning python tutorial"'),
    ):
        result = await _rewrite_query(
            original_query="ai courses",
            interests=["AI", "ML"],
            skill_level="Intermediate",
            intent="Upskilling"
        )

    assert result is not None
    assert len(result.split()) <= 10
    assert "advanced machine learning" in result


@pytest.mark.asyncio
async def test_rewrite_query_failure_fallback():
    """Test _rewrite_query returns None when LLM call fails."""
    from app.agent.nodes import _rewrite_query

    with patch(
        "app.agent.nodes.generate_chat_completion",
        new=AsyncMock(side_effect=Exception("API connection error")),
    ):
        result = await _rewrite_query(
            original_query="ai courses",
            interests=["AI"],
            skill_level="Beginner",
            intent="Learning"
        )

    assert result is None


@pytest.mark.asyncio
async def test_refetch_broaden_node_with_rewrite():
    """Test refetch_broaden_node updates search query, increments count, and sets drop_filters."""
    from app.agent.nodes import refetch_broaden_node

    with patch(
        "app.agent.nodes._rewrite_query",
        new=AsyncMock(return_value="expanded artificial intelligence machine learning course"),
    ):
        state = {
            "user_id": 1,
            "search_query": "ai courses",
            "user_profile": {"interests": ["AI"], "skill_level": "Intermediate", "intent": "Upskilling"},
            "refetch_count": 0
        }

        result = await refetch_broaden_node(state)

    assert result["refetch_count"] == 1
    assert result["drop_filters"] is True
    assert result["search_query"] == "expanded artificial intelligence machine learning course"


@pytest.mark.asyncio
async def test_refetch_broaden_node_fallback_when_rewrite_fails():
    """Test refetch_broaden_node falls back to original query if rewrite returns None."""
    from app.agent.nodes import refetch_broaden_node

    with patch("app.agent.nodes._rewrite_query", new=AsyncMock(return_value=None)):
        state = {
            "user_id": 1,
            "search_query": "original query",
            "user_profile": {"interests": ["AI"]},
            "refetch_count": 1
        }

        result = await refetch_broaden_node(state)

    assert result["refetch_count"] == 2
    assert result["drop_filters"] is True
    assert result["search_query"] == "original query"


@pytest.mark.asyncio
async def test_critique_passes():
    """Test critique node passes when narrative meets length and grounding requirements."""
    from app.agent.nodes import critique_narrative_node

    # Create narrative of ~130 words referencing "Python AI"
    words = ["word"] * 125
    narrative_text = "### Course Overview\nTake the **Python AI** course today. " + " ".join(words)

    mock_db = MagicMock()
    mock_product = MagicMock()
    mock_product.title = "Python AI"
    mock_db.query.return_value.filter.return_value.all.return_value = [mock_product]

    state = {
        "final_narrative": narrative_text,
        "recommended_product_ids": [1],
        "critique_retry_count": 0
    }

    with patch("app.agent.nodes.SessionLocal", return_value=mock_db):
        result = await critique_narrative_node(state)

    assert result["validation_passed"] is True
    assert result["critique_feedback"] == ""


@pytest.mark.asyncio
async def test_critique_fails_grounding():
    """Test critique node fails when narrative does not mention recommended product title."""
    from app.agent.nodes import critique_narrative_node

    words = ["generic"] * 140
    narrative_text = "### Generic Narrative\n" + " ".join(words)

    mock_db = MagicMock()
    mock_product = MagicMock()
    mock_product.title = "Unmentioned AI Course"
    mock_db.query.return_value.filter.return_value.all.return_value = [mock_product]

    state = {
        "final_narrative": narrative_text,
        "recommended_product_ids": [1],
        "critique_retry_count": 0
    }

    with patch("app.agent.nodes.SessionLocal", return_value=mock_db):
        result = await critique_narrative_node(state)

    assert result["validation_passed"] is False
    assert result["critique_retry_count"] == 1
    assert "does not mention any of the recommended courses" in result["critique_feedback"]


@pytest.mark.asyncio
async def test_critique_fails_length():
    """Test critique node fails when narrative is too short or too long."""
    from app.agent.nodes import critique_narrative_node

    short_narrative = "### Short Narrative\nPython AI course is great!"  # < 120 words

    mock_db = MagicMock()
    mock_product = MagicMock()
    mock_product.title = "Python AI"
    mock_db.query.return_value.filter.return_value.all.return_value = [mock_product]

    state = {
        "final_narrative": short_narrative,
        "recommended_product_ids": [1],
        "critique_retry_count": 0
    }

    with patch("app.agent.nodes.SessionLocal", return_value=mock_db):
        result = await critique_narrative_node(state)

    assert result["validation_passed"] is False
    assert "too short" in result["critique_feedback"]


def test_should_retry_or_store():
    """Test conditional critique edge logic for retrying vs storing."""
    # Case 1: Validation passed -> store
    assert should_retry_or_store({"validation_passed": True, "critique_retry_count": 1}) == "store"

    # Case 2: Validation failed on 1st attempt (retry_count == 1 after increment) -> retry generate
    assert should_retry_or_store({"validation_passed": False, "critique_retry_count": 1}) == "generate"

    # Case 3: Validation failed on 2nd attempt (retry_count == 2 after increment) -> store
    assert should_retry_or_store({"validation_passed": False, "critique_retry_count": 2}) == "store"


def test_build_recurring_pattern_summary_empty():
    """Test _build_recurring_pattern_summary returns default message when no events exist."""
    from app.agent.nodes import _build_recurring_pattern_summary

    assert _build_recurring_pattern_summary([]) == "No distinct recurring patterns detected over recent activity."


def test_build_recurring_pattern_summary_with_patterns():
    """Test _build_recurring_pattern_summary correctly counts repeated searches, categories, and actions."""
    from app.agent.nodes import _build_recurring_pattern_summary

    events = [
        {"event_type": "search", "payload_json": {"query": "LangGraph"}},
        {"event_type": "search", "payload_json": {"query": "LangGraph"}},
        {"event_type": "search", "payload_json": {"query": "langgraph"}},
        {"event_type": "course_view", "payload_json": {"category": "AI & Agents"}},
        {"event_type": "course_view", "payload_json": {"category": "AI & Agents"}},
        {"event_type": "wishlist", "payload_json": {"course_id": 1}},
        {"event_type": "wishlist", "payload_json": {"course_id": 2}},
        {"event_type": "page_view", "payload_json": {"path": "/courses"}},  # single view, shouldn't appear
    ]

    summary = _build_recurring_pattern_summary(events)

    assert "Searched for 'langgraph' 3 times." in summary
    assert "Viewed 'AI & Agents' category 2 times." in summary
    assert "Saved courses to wishlist 2 times." in summary
    assert "Visited platform pages" not in summary  # only 1 page_view event

def test_generate_template_reasons():
    """Test _generate_template_reasons produces deterministic fallback reasons."""
    from app.agent.nodes import _generate_template_reasons

    mock_product = MagicMock()
    mock_product.category = "Machine Learning"

    with patch("app.agent.nodes.SessionLocal") as mock_session_cls, \
         patch("app.agent.nodes.get_product", return_value=mock_product):
        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db

        reasons = _generate_template_reasons(
            product_ids=[1, 2, 3],
            user_profile={"skill_level": "Intermediate", "interests": ["AI"]},
            search_query="deep learning"
        )

    assert len(reasons) == 3
    assert "deep learning" in reasons[0]          # template 0: matched search
    assert "Machine Learning" in reasons[1]       # template 1: category
    assert "Intermediate" in reasons[2]           # template 2: skill level

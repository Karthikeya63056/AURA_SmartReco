import pytest
from app.agent.graph import should_refetch


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

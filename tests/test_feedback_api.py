import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "web_runs"))

from feedback_utils import parse_feedback_page


def test_feedback_page_rejects_non_integer():
    with pytest.raises(ValueError, match="page must be a positive integer"):
        parse_feedback_page("' UNION SELECT NULL--")


def test_feedback_page_rejects_zero_and_negative_values():
    for raw_page in ("0", "-1"):
        with pytest.raises(ValueError, match="page must be a positive integer"):
            parse_feedback_page(raw_page)


def test_feedback_page_defaults_are_handled_by_the_route():
    assert parse_feedback_page("1") == 1

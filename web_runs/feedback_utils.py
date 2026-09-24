def parse_feedback_page(raw_page):
    """Validate the public feedback pagination parameter."""
    try:
        page = int(raw_page)
    except (TypeError, ValueError):
        raise ValueError("page must be a positive integer")
    if page < 1:
        raise ValueError("page must be a positive integer")
    return page

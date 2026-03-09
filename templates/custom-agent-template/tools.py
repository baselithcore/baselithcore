"""
Tools for MyAgent.

Define functions that the agent can invoke.
"""


def tool(func):
    """Decorator to mark a function as an agent tool."""
    func._is_tool = True
    func._tool_name = func.__name__
    func._tool_description = func.__doc__ or ""
    return func


@tool
def search_knowledge_base(query: str) -> list[dict]:
    """
    Search the internal knowledge base.

    Args:
        query: Search query string

    Returns:
        List of matching documents
    """
    # Implement actual search
    return [{"title": "Example Result", "content": "Sample content matching query"}]


@tool
def get_current_time() -> str:
    """
    Get the current date and time.

    Returns:
        ISO formatted datetime string
    """
    from datetime import datetime

    return datetime.now().isoformat()


@tool
def calculate(expression: str) -> float:
    """
    Evaluate a mathematical expression.

    Args:
        expression: Math expression to evaluate (e.g., "2 + 2 * 3")

    Returns:
        Result of the calculation
    """
    # WARNING: In production, use a safe evaluator
    try:
        # Only allow safe operations
        allowed = set("0123456789+-*/().  ")
        if set(expression) <= allowed:
                return eval(expression)  # nosec B307
        raise ValueError("Invalid characters in expression")
    except Exception:
        return float("nan")


def get_all_tools() -> list:
    """Get all registered tools."""
    import sys

    module = sys.modules[__name__]
    tools = []
    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and hasattr(obj, "_is_tool"):
            tools.append(obj)
    return tools

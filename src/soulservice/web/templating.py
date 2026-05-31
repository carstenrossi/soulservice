import json
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates


def _role_context(request: Request) -> dict:
    """Expose the session role + RBAC flags to every template."""
    role = "viewer"
    try:
        role = request.session.get("admin_role", "viewer")
    except (AssertionError, KeyError):
        # No session (e.g. SessionMiddleware not active in a unit context).
        role = "viewer"
    return {
        "role": role,
        "can_edit": role in ("editor", "admin"),
        "can_admin": role == "admin",
    }


templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates"),
    context_processors=[_role_context],
)


def _tojson_filter(value, indent=2):
    return json.dumps(value, indent=indent, ensure_ascii=False)


templates.env.filters["tojson"] = _tojson_filter

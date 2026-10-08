from fastapi import Header, HTTPException
from backend.app.config import ROLES


def current_role(x_role: str = Header(default="QA reviewer")) -> str:
    if x_role not in ROLES:
        raise HTTPException(403, "Unknown role. Select one of the demo roles.")
    return x_role


def require(role: str, *allowed: str) -> None:
    if role not in allowed:
        raise HTTPException(
            403, "This action belongs to " + ", ".join(allowed) + ". Switch to that role."
        )

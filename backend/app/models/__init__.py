"""SQLModel table definitions.

Importing this package registers every table on ``SQLModel.metadata`` so
Alembic autogenerate and ``create_all`` (tests) see the full schema.
"""

from .conversation import Conversation, Message
from .system_config import SystemConfig
from .user import User, UserRole

__all__ = [
    "Conversation",
    "Message",
    "SystemConfig",
    "User",
    "UserRole",
]

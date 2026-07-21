import uuid

from sqlalchemy.orm import Session

from apps.api.app.models.entities import User

DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def ensure_default_user(session: Session) -> User:
    user = session.get(User, DEFAULT_USER_ID)
    if user is None:
        user = User(id=DEFAULT_USER_ID, display_name="默认用户")
        session.add(user)
        session.flush()
    return user

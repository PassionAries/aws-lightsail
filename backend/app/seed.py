import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from app.security import hash_password

logger = logging.getLogger(__name__)


def seed_admin(db: Session) -> None:
    """仅在用户表为空时创建默认管理员。"""
    count = db.query(User).count()
    if count > 0:
        return
    settings = get_settings()
    admin = User(
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        is_admin=True,
    )
    db.add(admin)
    db.commit()
    logger.warning(
        "已创建默认管理员账号 username=%s（请尽快修改密码）",
        settings.admin_username,
    )

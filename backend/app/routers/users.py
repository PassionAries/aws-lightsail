from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_admin
from app.models import User
from app.schemas import UserCreate, UserOut, UserUpdate
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        monthly_limit_gb=user.monthly_limit_gb,
        auto_stop_on_limit_default=bool(getattr(user, "auto_stop_on_limit_default", False)),
        created_at=user.created_at,
        has_credentials=bool(user.credentials),
        credential_count=len(user.credentials or []),
    )


@router.get("", response_model=list[UserOut])
def list_users(
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> list[UserOut]:
    users = db.query(User).order_by(User.id.asc()).all()
    return [_user_out(u) for u in users]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    exists = db.query(User).filter(User.username == body.username).first()
    if exists:
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        is_admin=body.is_admin,
        monthly_limit_gb=body.monthly_limit_gb,
        auto_stop_on_limit_default=bool(body.auto_stop_on_limit_default),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if "monthly_limit_gb" in body.model_fields_set:
        user.monthly_limit_gb = body.monthly_limit_gb
    if "auto_stop_on_limit_default" in body.model_fields_set and body.auto_stop_on_limit_default is not None:
        user.auto_stop_on_limit_default = bool(body.auto_stop_on_limit_default)
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="不能删除当前登录的管理员")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.delete(user)
    db.commit()
    return {"message": "用户已删除"}

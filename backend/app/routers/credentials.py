from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import AwsCredential, OperationLog, User
from app.schemas import (
    CredentialCreate,
    CredentialItem,
    CredentialOut,
    CredentialUpdate,
)
from app.security import decrypt_secret, encrypt_secret, mask_access_key
from app.services import lightsail as ls

router = APIRouter(prefix="/credentials", tags=["credentials"])


def _remaining_vcpu(cred: AwsCredential) -> float | None:
    if cred.vcpu_quota is None or cred.used_vcpu is None:
        return None
    try:
        return max(0.0, float(cred.vcpu_quota) - float(cred.used_vcpu))
    except (TypeError, ValueError):
        return None


def _item(cred: AwsCredential) -> CredentialItem:
    try:
        ak = decrypt_secret(cred.access_key_id_enc)
        masked = mask_access_key(ak)
    except Exception:  # noqa: BLE001
        masked = "****"
    return CredentialItem(
        id=cred.id,
        access_key_masked=masked,
        account_label=cred.account_label,
        is_default=bool(cred.is_default),
        last_validated_at=cred.last_validated_at,
        created_at=cred.created_at,
        vcpu_quota=cred.vcpu_quota,
        vcpu_tier=cred.vcpu_tier,
        static_ip_quota=cred.static_ip_quota,
        used_vcpu=cred.used_vcpu,
        used_instance_count=cred.used_instance_count,
        remaining_vcpu=_remaining_vcpu(cred),
        quota_region=cred.quota_region,
        quota_message=cred.quota_message,
        quota_checked_at=cred.quota_checked_at,
    )


def _apply_quota_to_cred(cred: AwsCredential, ak: str, sk: str, region: str = "us-east-1") -> None:
    """查询并写回 Lightsail 配额字段（失败不抛，写入 message）。"""
    info = ls.get_lightsail_account_quotas(ak, sk, region=region, include_usage=True)
    cred.vcpu_quota = info.get("vcpu_quota")
    cred.vcpu_tier = info.get("vcpu_tier")
    cred.static_ip_quota = info.get("static_ip_quota")
    cred.used_vcpu = info.get("used_vcpu")
    cred.used_instance_count = info.get("used_instance_count")
    cred.quota_region = info.get("region") or region
    msg = info.get("message")
    if info.get("error") and not msg:
        msg = f"配额查询失败: {info['error']}"
    cred.quota_message = msg
    cred.quota_checked_at = datetime.now(timezone.utc)


def _list_out(user: User) -> CredentialOut:
    items = [_item(c) for c in sorted(user.credentials, key=lambda x: (not x.is_default, x.id))]
    default = next((i for i in items if i.is_default), items[0] if items else None)
    return CredentialOut(
        has_credentials=bool(items),
        items=items,
        id=default.id if default else None,
        access_key_masked=default.access_key_masked if default else None,
        account_label=default.account_label if default else None,
        last_validated_at=default.last_validated_at if default else None,
    )


def _clear_other_defaults(db: Session, user_id: int, keep_id: int | None = None) -> None:
    q = db.query(AwsCredential).filter(AwsCredential.user_id == user_id)
    if keep_id is not None:
        q = q.filter(AwsCredential.id != keep_id)
    for c in q.all():
        if c.is_default:
            c.is_default = False
            db.add(c)


@router.get("", response_model=CredentialOut)
def list_credentials(user: User = Depends(get_current_user)) -> CredentialOut:
    return _list_out(user)


@router.post("", response_model=CredentialOut, status_code=201)
def create_credential(
    body: CredentialCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialOut:
    ak = body.access_key_id.strip()
    sk = body.secret_access_key.strip()
    try:
        ls.validate_credentials(ak, sk)
    except ls.LightsailError as exc:
        raise HTTPException(status_code=400, detail=f"AWS 凭证校验失败: {exc.message}")

    now = datetime.now(timezone.utc)
    make_default = body.is_default or not user.credentials
    if make_default:
        _clear_other_defaults(db, user.id)

    cred = AwsCredential(
        user_id=user.id,
        access_key_id_enc=encrypt_secret(ak),
        secret_access_key_enc=encrypt_secret(sk),
        account_label=body.account_label,
        is_default=make_default,
        last_validated_at=now,
    )
    _apply_quota_to_cred(cred, ak, sk)
    db.add(cred)
    db.add(
        OperationLog(
            user_id=user.id,
            action="add_credentials",
            status="success",
            message=body.account_label or mask_access_key(body.access_key_id.strip()),
        )
    )
    db.commit()
    db.refresh(user)
    return _list_out(user)


@router.put("/{credential_id}", response_model=CredentialOut)
def update_credential(
    credential_id: int,
    body: CredentialUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialOut:
    cred = (
        db.query(AwsCredential)
        .filter(AwsCredential.id == credential_id, AwsCredential.user_id == user.id)
        .first()
    )
    if not cred:
        raise HTTPException(status_code=404, detail="凭证不存在")

    # 若更新密钥则校验
    if body.access_key_id or body.secret_access_key:
        try:
            old_ak = decrypt_secret(cred.access_key_id_enc)
            old_sk = decrypt_secret(cred.secret_access_key_enc)
        except Exception:  # noqa: BLE001
            old_ak, old_sk = "", ""
        ak = (body.access_key_id or old_ak).strip()
        sk = (body.secret_access_key or old_sk).strip()
        try:
            ls.validate_credentials(ak, sk)
        except ls.LightsailError as exc:
            raise HTTPException(status_code=400, detail=f"AWS 凭证校验失败: {exc.message}")
        if body.access_key_id:
            cred.access_key_id_enc = encrypt_secret(body.access_key_id.strip())
        if body.secret_access_key:
            cred.secret_access_key_enc = encrypt_secret(body.secret_access_key.strip())
        cred.last_validated_at = datetime.now(timezone.utc)
        _apply_quota_to_cred(cred, ak, sk)

    if body.account_label is not None:
        cred.account_label = body.account_label
    if body.is_default is True:
        _clear_other_defaults(db, user.id, keep_id=cred.id)
        cred.is_default = True
    elif body.is_default is False and cred.is_default:
        # 不允许没有任何默认：若取消，自动把另一组设为默认
        cred.is_default = False
        other = (
            db.query(AwsCredential)
            .filter(AwsCredential.user_id == user.id, AwsCredential.id != cred.id)
            .order_by(AwsCredential.id.asc())
            .first()
        )
        if other:
            other.is_default = True
            db.add(other)
        else:
            cred.is_default = True

    db.add(cred)
    db.commit()
    db.refresh(user)
    return _list_out(user)


@router.post("/{credential_id}/default", response_model=CredentialOut)
def set_default(
    credential_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialOut:
    cred = (
        db.query(AwsCredential)
        .filter(AwsCredential.id == credential_id, AwsCredential.user_id == user.id)
        .first()
    )
    if not cred:
        raise HTTPException(status_code=404, detail="凭证不存在")
    _clear_other_defaults(db, user.id, keep_id=cred.id)
    cred.is_default = True
    db.add(cred)
    db.commit()
    db.refresh(user)
    return _list_out(user)


@router.post("/{credential_id}/validate", response_model=CredentialItem)
def validate_one(
    credential_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialItem:
    cred = (
        db.query(AwsCredential)
        .filter(AwsCredential.id == credential_id, AwsCredential.user_id == user.id)
        .first()
    )
    if not cred:
        raise HTTPException(status_code=404, detail="凭证不存在")
    try:
        ak = decrypt_secret(cred.access_key_id_enc)
        sk = decrypt_secret(cred.secret_access_key_enc)
        ls.validate_credentials(ak, sk)
    except ls.LightsailError as exc:
        raise HTTPException(status_code=400, detail=f"校验失败: {exc.message}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))
    cred.last_validated_at = datetime.now(timezone.utc)
    _apply_quota_to_cred(cred, ak, sk)
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return _item(cred)


@router.post("/{credential_id}/quotas", response_model=CredentialItem)
def refresh_quotas(
    credential_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialItem:
    """检测该账号 Lightsail 配额（vCPU 档位如 5V/8V/32V，以及静态 IP 等）。"""
    cred = (
        db.query(AwsCredential)
        .filter(AwsCredential.id == credential_id, AwsCredential.user_id == user.id)
        .first()
    )
    if not cred:
        raise HTTPException(status_code=404, detail="凭证不存在")
    try:
        ak = decrypt_secret(cred.access_key_id_enc)
        sk = decrypt_secret(cred.secret_access_key_enc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"解密凭证失败: {exc}")
    _apply_quota_to_cred(cred, ak, sk)
    db.add(cred)
    db.add(
        OperationLog(
            user_id=user.id,
            action="refresh_quotas",
            status="success" if cred.vcpu_quota is not None else "partial",
            message=cred.quota_message or cred.vcpu_tier or f"credential #{credential_id}",
        )
    )
    db.commit()
    db.refresh(cred)
    return _item(cred)


@router.delete("/{credential_id}")
def delete_credential(
    credential_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    cred = (
        db.query(AwsCredential)
        .filter(AwsCredential.id == credential_id, AwsCredential.user_id == user.id)
        .first()
    )
    if not cred:
        raise HTTPException(status_code=404, detail="凭证不存在")
    was_default = cred.is_default
    db.delete(cred)
    db.flush()
    if was_default:
        other = (
            db.query(AwsCredential)
            .filter(AwsCredential.user_id == user.id)
            .order_by(AwsCredential.id.asc())
            .first()
        )
        if other:
            other.is_default = True
            db.add(other)
    db.add(
        OperationLog(
            user_id=user.id,
            action="delete_credentials",
            status="success",
            message=f"deleted credential #{credential_id}",
        )
    )
    db.commit()
    return {"message": "已删除凭证"}


# ---- 兼容旧接口：PUT /credentials 作为「新增或更新默认」----
@router.put("", response_model=CredentialOut)
def put_compat(
    body: CredentialCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialOut:
    """兼容：若无凭证则新增；若有默认凭证则更新默认那一组。"""
    ak = body.access_key_id.strip()
    sk = body.secret_access_key.strip()
    try:
        ls.validate_credentials(ak, sk)
    except ls.LightsailError as exc:
        raise HTTPException(status_code=400, detail=f"AWS 凭证校验失败: {exc.message}")

    now = datetime.now(timezone.utc)
    default = user.default_credential
    if default is None:
        cred = AwsCredential(
            user_id=user.id,
            access_key_id_enc=encrypt_secret(ak),
            secret_access_key_enc=encrypt_secret(sk),
            account_label=body.account_label,
            is_default=True,
            last_validated_at=now,
        )
        _apply_quota_to_cred(cred, ak, sk)
        db.add(cred)
        action = "add_credentials"
    else:
        default.access_key_id_enc = encrypt_secret(ak)
        default.secret_access_key_enc = encrypt_secret(sk)
        if body.account_label is not None:
            default.account_label = body.account_label
        default.last_validated_at = now
        _apply_quota_to_cred(default, ak, sk)
        db.add(default)
        action = "update_credentials"
    db.add(
        OperationLog(
            user_id=user.id,
            action=action,
            status="success",
            message=body.account_label or mask_access_key(body.access_key_id.strip()),
        )
    )
    db.commit()
    db.refresh(user)
    return _list_out(user)

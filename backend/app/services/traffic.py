from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import AwsCredential, InstanceSetting, TrafficUsage, User
from app.security import decrypt_secret
from app.services import lightsail as ls


class NoCredentialsError(Exception):
    pass


def get_credential(user: User, credential_id: int | None = None) -> AwsCredential:
    if not user.credentials:
        raise NoCredentialsError("请先绑定 AWS 凭证")
    if credential_id is not None:
        for c in user.credentials:
            if c.id == credential_id:
                return c
        raise NoCredentialsError(f"凭证不存在: {credential_id}")
    cred = user.default_credential
    if not cred:
        raise NoCredentialsError("请先绑定 AWS 凭证")
    return cred


def get_credential_keys(cred: AwsCredential) -> tuple[str, str]:
    ak = decrypt_secret(cred.access_key_id_enc)
    sk = decrypt_secret(cred.secret_access_key_enc)
    return ak, sk


def get_user_aws_keys(user: User, credential_id: int | None = None) -> tuple[str, str]:
    """返回 (access_key, secret_key)。兼容旧调用；多 Key 时默认用 is_default。"""
    cred = get_credential(user, credential_id)
    return get_credential_keys(cred)


def get_user_aws_keys_and_cred(
    user: User, credential_id: int | None = None
) -> tuple[str, str, AwsCredential]:
    cred = get_credential(user, credential_id)
    ak, sk = get_credential_keys(cred)
    return ak, sk, cred


def bytes_to_gb(value: int | float) -> float:
    return round(float(value) / (1024 ** 3), 2)


def current_year_month(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def month_start_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def effective_limit_gb(user: User, setting: InstanceSetting | None) -> float | None:
    if setting is not None and setting.monthly_limit_gb is not None:
        return setting.monthly_limit_gb
    return user.monthly_limit_gb


def effective_auto_stop(user: User, setting: InstanceSetting | None) -> bool:
    if setting is not None:
        return bool(setting.auto_stop_on_limit)
    return bool(user.auto_stop_on_limit_default)


def get_setting(
    db: Session,
    user_id: int,
    region: str,
    instance_name: str,
    credential_id: int | None = None,
) -> InstanceSetting | None:
    q = db.query(InstanceSetting).filter(
        InstanceSetting.user_id == user_id,
        InstanceSetting.region == region,
        InstanceSetting.instance_name == instance_name,
    )
    if credential_id is not None:
        q = q.filter(InstanceSetting.credential_id == credential_id)
    return q.first()


def upsert_traffic(
    db: Session,
    user_id: int,
    credential_id: int,
    region: str,
    instance_name: str,
    year_month: str,
    network_in_bytes: int,
    network_out_bytes: int,
) -> TrafficUsage:
    row = (
        db.query(TrafficUsage)
        .filter(
            TrafficUsage.user_id == user_id,
            TrafficUsage.credential_id == credential_id,
            TrafficUsage.region == region,
            TrafficUsage.instance_name == instance_name,
            TrafficUsage.year_month == year_month,
        )
        .first()
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = TrafficUsage(
            user_id=user_id,
            credential_id=credential_id,
            region=region,
            instance_name=instance_name,
            year_month=year_month,
            network_in_bytes=network_in_bytes,
            network_out_bytes=network_out_bytes,
            last_synced_at=now,
        )
        db.add(row)
    else:
        # 全量覆盖，禁止增量累加
        row.network_in_bytes = network_in_bytes
        row.network_out_bytes = network_out_bytes
        row.last_synced_at = now
        db.add(row)
    return row


def build_traffic_summary(
    db: Session, user: User, credential_id: int | None = None
) -> dict:
    ym = current_year_month()
    q = db.query(TrafficUsage).filter(
        TrafficUsage.user_id == user.id, TrafficUsage.year_month == ym
    )
    if credential_id is not None:
        q = q.filter(TrafficUsage.credential_id == credential_id)
    rows = q.all()

    settings = db.query(InstanceSetting).filter(InstanceSetting.user_id == user.id).all()
    setting_map = {
        (s.credential_id, s.region, s.instance_name): s for s in settings
    }
    cred_label = {c.id: c.account_label for c in user.credentials}

    instances = []
    region_totals: dict[str, dict] = {}

    for r in rows:
        setting = setting_map.get((r.credential_id, r.region, r.instance_name))
        limit = effective_limit_gb(user, setting)
        auto_stop = effective_auto_stop(user, setting)
        total_bytes = (r.network_in_bytes or 0) + (r.network_out_bytes or 0)
        total_gb = bytes_to_gb(total_bytes)
        over = limit is not None and total_gb > limit
        instances.append(
            {
                "region": r.region,
                "name": r.instance_name,
                "credential_id": r.credential_id,
                "account_label": cred_label.get(r.credential_id),
                "in_gb": bytes_to_gb(r.network_in_bytes or 0),
                "out_gb": bytes_to_gb(r.network_out_bytes or 0),
                "total_gb": total_gb,
                "limit_gb": limit,
                "over_limit": over,
                "auto_stop_on_limit": auto_stop,
                "year_month": ym,
            }
        )
        bucket = region_totals.setdefault(
            r.region, {"region": r.region, "total_gb": 0.0, "instance_count": 0}
        )
        bucket["total_gb"] = round(bucket["total_gb"] + total_gb, 2)
        bucket["instance_count"] += 1

    by_region = sorted(region_totals.values(), key=lambda x: x["region"])
    instances.sort(key=lambda x: (x["region"], x["name"], x.get("credential_id") or 0))
    return {
        "year_month": ym,
        "instances": instances,
        "by_region": by_region,
        "note": "流量基于 Lightsail NetworkIn/NetworkOut 指标估算，与账单可能存在差异",
    }


def traffic_for_instance(
    db: Session,
    user: User,
    region: str,
    name: str,
    credential_id: int | None = None,
) -> dict | None:
    ym = current_year_month()
    q = db.query(TrafficUsage).filter(
        TrafficUsage.user_id == user.id,
        TrafficUsage.region == region,
        TrafficUsage.instance_name == name,
        TrafficUsage.year_month == ym,
    )
    if credential_id is not None:
        q = q.filter(TrafficUsage.credential_id == credential_id)
    row = q.first()
    setting = get_setting(db, user.id, region, name, credential_id)
    limit = effective_limit_gb(user, setting)
    if row is None:
        return {
            "in_gb": 0.0,
            "out_gb": 0.0,
            "total_gb": 0.0,
            "limit_gb": limit,
            "over_limit": False,
            "year_month": ym,
        }
    total = (row.network_in_bytes or 0) + (row.network_out_bytes or 0)
    total_gb = bytes_to_gb(total)
    return {
        "in_gb": bytes_to_gb(row.network_in_bytes or 0),
        "out_gb": bytes_to_gb(row.network_out_bytes or 0),
        "total_gb": total_gb,
        "limit_gb": limit,
        "over_limit": limit is not None and total_gb > limit,
        "year_month": ym,
    }

"""定时流量采集 + 可选超限自动关机。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import AwsCredential, OperationLog, User
from app.services import lightsail as ls
from app.services.traffic import (
    bytes_to_gb,
    current_year_month,
    effective_auto_stop,
    effective_limit_gb,
    get_credential_keys,
    get_setting,
    month_start_utc,
    upsert_traffic,
)

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _sync_credential(db: Session, user: User, cred: AwsCredential) -> int:
    try:
        ak, sk = get_credential_keys(cred)
    except Exception as exc:  # noqa: BLE001
        logger.warning("用户 %s 凭证 #%s 解密失败: %s", user.username, cred.id, exc)
        return 0

    try:
        regions = ls.get_regions(ak, sk)
    except ls.LightsailError as exc:
        logger.warning(
            "用户 %s 凭证 #%s 获取区域失败: %s", user.username, cred.id, exc.message
        )
        return 0

    now = datetime.now(timezone.utc)
    start = month_start_utc(now)
    ym = current_year_month(now)
    count = 0

    for r in regions:
        region = r["name"]
        try:
            instances = ls.list_instances_in_region(ak, sk, region)
        except ls.LightsailError as exc:
            logger.warning(
                "用户 %s 凭证 #%s 区域 %s 列实例失败: %s",
                user.username,
                cred.id,
                region,
                exc.message,
            )
            continue

        for inst in instances:
            name = inst.get("name")
            if not name:
                continue
            try:
                inn = ls.get_metric_sum_bytes(
                    ak, sk, region, name, "NetworkIn", start, now, 86400
                )
                out = ls.get_metric_sum_bytes(
                    ak, sk, region, name, "NetworkOut", start, now, 86400
                )
                upsert_traffic(db, user.id, cred.id, region, name, ym, inn, out)
                count += 1

                # 可选：超限且勾选了 auto_stop 才关机
                setting = get_setting(db, user.id, region, name, cred.id)
                limit = effective_limit_gb(user, setting)
                auto_stop = effective_auto_stop(user, setting)
                total_gb = bytes_to_gb((inn or 0) + (out or 0))
                state = (inst.get("state") or "").lower()
                if (
                    auto_stop
                    and limit is not None
                    and total_gb > limit
                    and state == "running"
                ):
                    try:
                        ls.stop_instance(ak, sk, region, name)
                        db.add(
                            OperationLog(
                                user_id=user.id,
                                action="auto_stop_on_limit",
                                region=region,
                                target=name,
                                status="success",
                                message=(
                                    f"超限自动关机: {total_gb}GB > {limit}GB "
                                    f"(credential=#{cred.id})"
                                ),
                            )
                        )
                        logger.warning(
                            "超限自动关机 user=%s %s/%s %.2fGB > %.2fGB",
                            user.username,
                            region,
                            name,
                            total_gb,
                            limit,
                        )
                    except ls.LightsailError as exc:
                        db.add(
                            OperationLog(
                                user_id=user.id,
                                action="auto_stop_on_limit",
                                region=region,
                                target=name,
                                status="error",
                                message=exc.message,
                            )
                        )
                        logger.warning(
                            "超限自动关机失败 user=%s %s/%s: %s",
                            user.username,
                            region,
                            name,
                            exc.message,
                        )
            except ls.LightsailError as exc:
                logger.warning(
                    "采集流量失败 user=%s cred=#%s %s/%s: %s",
                    user.username,
                    cred.id,
                    region,
                    name,
                    exc.message,
                )
    return count


def sync_user_traffic(db: Session, user: User) -> int:
    """同步用户全部凭证下的实例当月流量。返回成功写入条数。"""
    if not user.credentials:
        return 0
    total = 0
    for cred in list(user.credentials):
        total += _sync_credential(db, user, cred)
    if total:
        db.commit()
    return total


def sync_all_users() -> None:
    db = SessionLocal()
    try:
        # 有任意凭证的用户
        user_ids = [r[0] for r in db.query(AwsCredential.user_id).distinct().all()]
        users = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
        total = 0
        for user in users:
            try:
                # 确保加载 credentials
                _ = list(user.credentials)
                total += sync_user_traffic(db, user)
            except Exception:  # noqa: BLE001
                logger.exception("采集用户 %s 流量异常", user.username)
                db.rollback()
        logger.info("流量采集完成，写入 %s 条记录", total)
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        sync_all_users,
        "interval",
        minutes=max(5, int(settings.collect_interval_minutes or 60)),
        id="traffic_collector",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "流量采集调度已启动，间隔 %s 分钟",
        settings.collect_interval_minutes,
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

from __future__ import annotations

import asyncio
import logging
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import InstanceSetting, OperationLog, User
from app.schemas import (
    ChangeIpResponse,
    CreateInstanceResponse,
    InstanceCreate,
    InstanceOut,
    InstanceSettingsUpdate,
    InstanceTraffic,
    OperationResult,
)
from app.services import lightsail as ls
from app.services.traffic import (
    NoCredentialsError,
    effective_auto_stop,
    effective_limit_gb,
    get_setting,
    get_user_aws_keys_and_cred,
    traffic_for_instance,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/instances", tags=["instances"])

_op_locks: dict[str, Lock] = {}
_op_locks_guard = Lock()


def _lock_for(user_id: int, credential_id: int, region: str, name: str) -> Lock:
    key = f"{user_id}:{credential_id}:{region}:{name}"
    with _op_locks_guard:
        if key not in _op_locks:
            _op_locks[key] = Lock()
        return _op_locks[key]


def _keys(user: User, credential_id: int | None = None):
    try:
        return get_user_aws_keys_and_cred(user, credential_id)
    except NoCredentialsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _log(
    db: Session,
    user_id: int,
    action: str,
    region: str | None,
    target: str | None,
    status: str,
    message: str | None = None,
) -> None:
    db.add(
        OperationLog(
            user_id=user_id,
            action=action,
            region=region,
            target=target,
            status=status,
            message=message,
        )
    )


def _to_out(
    db: Session,
    user: User,
    raw: dict,
    credential_id: int,
    account_label: str | None,
) -> InstanceOut:
    region = raw["region"]
    name = raw["name"]
    setting = get_setting(db, user.id, region, name, credential_id)
    traffic = traffic_for_instance(db, user, region, name, credential_id) or {}
    limit = effective_limit_gb(user, setting)
    if traffic.get("limit_gb") is None:
        traffic["limit_gb"] = limit
        traffic["over_limit"] = limit is not None and traffic.get("total_gb", 0) > limit
    return InstanceOut(
        name=name,
        region=region,
        availability_zone=raw.get("availability_zone"),
        state=raw.get("state") or "unknown",
        public_ip=raw.get("public_ip"),
        private_ip=raw.get("private_ip"),
        blueprint_id=raw.get("blueprint_id"),
        blueprint_name=raw.get("blueprint_name"),
        bundle_id=raw.get("bundle_id"),
        is_static_ip=bool(raw.get("is_static_ip")),
        static_ip_name=raw.get("static_ip_name"),
        created_at=raw.get("created_at"),
        traffic=InstanceTraffic(**traffic) if traffic else None,
        monthly_limit_gb=setting.monthly_limit_gb if setting else None,
        auto_stop_on_limit=effective_auto_stop(user, setting),
        note=setting.note if setting else None,
        credential_id=credential_id,
        account_label=account_label,
    )


@router.get("", response_model=list[InstanceOut])
async def list_instances(
    credential_id: int | None = Query(None, description="仅扫描指定凭证；空=全部凭证"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[InstanceOut]:
    if not user.credentials:
        raise HTTPException(status_code=400, detail="请先绑定 AWS 凭证")

    creds = list(user.credentials)
    if credential_id is not None:
        creds = [c for c in creds if c.id == credential_id]
        if not creds:
            raise HTTPException(status_code=404, detail="凭证不存在")

    sem = asyncio.Semaphore(5)
    raw_items: list[tuple[int, str | None, dict]] = []
    errors: list[str] = []

    async def fetch_for_cred(cred) -> tuple[list[tuple[int, str | None, dict]], list[str]]:
        local_items: list[tuple[int, str | None, dict]] = []
        local_errors: list[str] = []
        try:
            ak, sk, _ = get_user_aws_keys_and_cred(user, cred.id)
            regions = await asyncio.to_thread(ls.get_regions, ak, sk)
        except (NoCredentialsError, RuntimeError, ls.LightsailError) as exc:
            msg = getattr(exc, "message", str(exc))
            return [], [f"凭证#{cred.id}: {msg}"]
        except Exception as exc:  # noqa: BLE001
            return [], [f"凭证#{cred.id}: {exc}"]

        async def fetch_region(region_name: str) -> tuple[str, list[dict] | str]:
            async with sem:
                try:
                    items = await asyncio.to_thread(
                        ls.list_instances_in_region, ak, sk, region_name
                    )
                    return region_name, items
                except ls.LightsailError as exc:
                    return region_name, exc.message

        results = await asyncio.gather(*[fetch_region(r["name"]) for r in regions])
        for region_name, payload in results:
            if isinstance(payload, str):
                local_errors.append(f"凭证#{cred.id}/{region_name}: {payload}")
                continue
            for raw in payload:
                local_items.append((cred.id, cred.account_label, raw))
        return local_items, local_errors

    gathered = await asyncio.gather(*[fetch_for_cred(c) for c in creds])
    for items, errs in gathered:
        raw_items.extend(items)
        errors.extend(errs)

    # SQLAlchemy Session 非并发安全：统一在主协程组装输出
    instances = [
        _to_out(db, user, raw, cred_id, label) for cred_id, label, raw in raw_items
    ]
    instances.sort(key=lambda x: (x.region, x.name, x.credential_id or 0))
    if not instances and errors:
        raise HTTPException(status_code=400, detail="获取实例失败: " + "; ".join(errors[:8]))
    return instances


@router.get("/{region}/{name}", response_model=InstanceOut)
async def get_instance(
    region: str,
    name: str,
    credential_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InstanceOut:
    ak, sk, cred = _keys(user, credential_id)
    try:
        raw = await asyncio.to_thread(ls.get_instance, ak, sk, region, name)
    except ls.LightsailError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    return _to_out(db, user, raw, cred.id, cred.account_label)


@router.post("", response_model=CreateInstanceResponse)
async def create_instance(
    body: InstanceCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateInstanceResponse:
    ak, sk, cred = _keys(user, body.credential_id)
    lock = _lock_for(user.id, cred.id, body.region, body.instance_name)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该实例正在执行其他操作，请稍后")
    try:
        result = await asyncio.to_thread(
            ls.create_instance,
            ak,
            sk,
            body.region,
            body.instance_name,
            body.blueprint_id,
            body.bundle_id,
            body.availability_zone,
            body.allocate_static_ip,
            body.password,
            body.platform,
            body.open_all_ports,
        )
        # 写入默认设置（含用户级 auto_stop 默认）
        setting = get_setting(db, user.id, body.region, body.instance_name, cred.id)
        if setting is None:
            setting = InstanceSetting(
                user_id=user.id,
                credential_id=cred.id,
                region=body.region,
                instance_name=body.instance_name,
                auto_stop_on_limit=bool(user.auto_stop_on_limit_default),
            )
            db.add(setting)
        _log(
            db,
            user.id,
            "create_instance",
            body.region,
            body.instance_name,
            "success",
            f"{result.get('message')} (credential=#{cred.id})",
        )
        db.commit()
        return CreateInstanceResponse(
            name=result["name"],
            region=result["region"],
            credential_id=cred.id,
            static_ip_name=result.get("static_ip_name"),
            message=result.get("message") or "已提交",
        )
    except ls.LightsailError as exc:
        _log(db, user.id, "create_instance", body.region, body.instance_name, "error", exc.message)
        db.commit()
        raise HTTPException(status_code=400, detail=exc.message)
    finally:
        lock.release()


@router.post("/{region}/{name}/start", response_model=OperationResult)
async def start_instance(
    region: str,
    name: str,
    credential_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OperationResult:
    return await _simple_op(user, db, region, name, "start", ls.start_instance, credential_id)


@router.post("/{region}/{name}/stop", response_model=OperationResult)
async def stop_instance(
    region: str,
    name: str,
    credential_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OperationResult:
    return await _simple_op(user, db, region, name, "stop", ls.stop_instance, credential_id)


@router.post("/{region}/{name}/reboot", response_model=OperationResult)
async def reboot_instance(
    region: str,
    name: str,
    credential_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OperationResult:
    return await _simple_op(user, db, region, name, "reboot", ls.reboot_instance, credential_id)


async def _simple_op(
    user: User,
    db: Session,
    region: str,
    name: str,
    action: str,
    fn,
    credential_id: int | None,
) -> OperationResult:
    ak, sk, cred = _keys(user, credential_id)
    lock = _lock_for(user.id, cred.id, region, name)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该实例正在执行其他操作，请稍后")
    labels = {"start": "开机", "stop": "关机", "reboot": "重启"}
    try:
        await asyncio.to_thread(fn, ak, sk, region, name)
        msg = f"{labels.get(action, action)}请求已提交，状态稍后刷新"
        _log(db, user.id, action, region, name, "success", msg)
        db.commit()
        return OperationResult(message=msg, region=region, name=name)
    except ls.LightsailError as exc:
        _log(db, user.id, action, region, name, "error", exc.message)
        db.commit()
        raise HTTPException(status_code=400, detail=exc.message)
    finally:
        lock.release()


@router.post("/{region}/{name}/change-ip", response_model=ChangeIpResponse)
async def change_ip(
    region: str,
    name: str,
    credential_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChangeIpResponse:
    ak, sk, cred = _keys(user, credential_id)
    lock = _lock_for(user.id, cred.id, region, name)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该实例正在执行其他操作，请稍后")
    try:
        result = await asyncio.to_thread(ls.change_static_ip, ak, sk, region, name)
        _log(
            db,
            user.id,
            "change_ip",
            region,
            name,
            "success",
            f"{result.get('old_ip')} -> {result.get('new_ip')} (cred=#{cred.id})",
        )
        db.commit()
        return ChangeIpResponse(**result)
    except ls.LightsailError as exc:
        status = 409 if exc.code == "Pending" else 400
        _log(db, user.id, "change_ip", region, name, "error", exc.message)
        db.commit()
        raise HTTPException(status_code=status, detail=exc.message)
    finally:
        lock.release()


@router.delete("/{region}/{name}", response_model=OperationResult)
async def delete_instance(
    region: str,
    name: str,
    credential_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OperationResult:
    ak, sk, cred = _keys(user, credential_id)
    lock = _lock_for(user.id, cred.id, region, name)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该实例正在执行其他操作，请稍后")
    try:
        result = await asyncio.to_thread(
            ls.delete_instance_and_static_ips, ak, sk, region, name
        )
        setting = get_setting(db, user.id, region, name, cred.id)
        if setting:
            db.delete(setting)
        _log(db, user.id, "delete_instance", region, name, "success", result.get("message"))
        db.commit()
        return OperationResult(message=result.get("message") or "已删除", region=region, name=name)
    except ls.LightsailError as exc:
        _log(db, user.id, "delete_instance", region, name, "error", exc.message)
        db.commit()
        raise HTTPException(status_code=400, detail=exc.message)
    finally:
        lock.release()


@router.patch("/{region}/{name}/settings", response_model=InstanceOut)
async def update_settings(
    region: str,
    name: str,
    body: InstanceSettingsUpdate,
    credential_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InstanceOut:
    ak, sk, cred = _keys(user, credential_id)
    setting = get_setting(db, user.id, region, name, cred.id)
    if setting is None:
        setting = InstanceSetting(
            user_id=user.id,
            credential_id=cred.id,
            region=region,
            instance_name=name,
            auto_stop_on_limit=bool(user.auto_stop_on_limit_default),
        )
        db.add(setting)
    if "monthly_limit_gb" in body.model_fields_set:
        setting.monthly_limit_gb = body.monthly_limit_gb
    if "auto_stop_on_limit" in body.model_fields_set and body.auto_stop_on_limit is not None:
        setting.auto_stop_on_limit = bool(body.auto_stop_on_limit)
    if "note" in body.model_fields_set:
        setting.note = body.note
    db.add(setting)
    db.commit()

    try:
        raw = await asyncio.to_thread(ls.get_instance, ak, sk, region, name)
        return _to_out(db, user, raw, cred.id, cred.account_label)
    except ls.LightsailError:
        traffic = traffic_for_instance(db, user, region, name, cred.id)
        return InstanceOut(
            name=name,
            region=region,
            state="unknown",
            traffic=InstanceTraffic(**traffic) if traffic else None,
            monthly_limit_gb=setting.monthly_limit_gb,
            auto_stop_on_limit=bool(setting.auto_stop_on_limit),
            note=setting.note,
            credential_id=cred.id,
            account_label=cred.account_label,
        )

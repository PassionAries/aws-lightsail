"""Lightsail boto3 封装。所有调用使用调用方传入的用户密钥，不用环境默认凭证。"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


class LightsailError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code


def make_client(access_key_id: str, secret_access_key: str, region: str = "us-east-1"):
    return boto3.client(
        "lightsail",
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )


def _raise_from_aws(exc: Exception) -> None:
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        code = err.get("Code", "ClientError")
        msg = err.get("Message", str(exc))
        raise LightsailError(f"AWS 错误 [{code}]: {msg}", code=code) from exc
    if isinstance(exc, BotoCoreError):
        raise LightsailError(f"AWS 连接错误: {exc}") from exc
    raise LightsailError(str(exc)) from exc


def validate_credentials(access_key_id: str, secret_access_key: str) -> None:
    try:
        client = make_client(access_key_id, secret_access_key, "us-east-1")
        client.get_regions(includeAvailabilityZones=False)
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)


def get_regions(access_key_id: str, secret_access_key: str) -> list[dict[str, Any]]:
    try:
        client = make_client(access_key_id, secret_access_key, "us-east-1")
        resp = client.get_regions(includeAvailabilityZones=True)
        regions = []
        for r in resp.get("regions", []):
            if not r.get("name"):
                continue
            regions.append(
                {
                    "name": r["name"],
                    "display_name": r.get("displayName") or r["name"],
                    "continent_code": r.get("continentCode"),
                    "availability_zones": [
                        az.get("zoneName")
                        for az in r.get("availabilityZones", [])
                        if az.get("zoneName") and az.get("state") == "available"
                    ],
                }
            )
        return regions
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)
        return []


def get_blueprints(access_key_id: str, secret_access_key: str, region: str) -> list[dict[str, Any]]:
    try:
        client = make_client(access_key_id, secret_access_key, region)
        items: list[dict[str, Any]] = []
        page_token = None
        while True:
            kwargs = {}
            if page_token:
                kwargs["pageToken"] = page_token
            resp = client.get_blueprints(**kwargs)
            for b in resp.get("blueprints", []):
                blueprint_id = b.get("blueprintId") or ""
                version = b.get("version") or ""
                # 部分蓝图 version 为空时，从 id 中兜底解析，如 ubuntu_22_04 / debian_12
                if not version and blueprint_id:
                    # 常见形态: name_major_minor 或 name_major
                    parts = blueprint_id.replace("-", "_").split("_")
                    ver_parts = [p for p in parts[1:] if p.isdigit() or (p.replace(".", "", 1).isdigit())]
                    if ver_parts:
                        version = ".".join(ver_parts)
                items.append(
                    {
                        "blueprint_id": blueprint_id,
                        "name": b.get("name") or blueprint_id,
                        "group": b.get("group"),
                        "type": b.get("type"),
                        "platform": b.get("platform"),
                        "version": version or None,
                        "is_active": bool(b.get("isActive", True)),
                    }
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return items
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)
        return []


def get_bundles(access_key_id: str, secret_access_key: str, region: str) -> list[dict[str, Any]]:
    try:
        client = make_client(access_key_id, secret_access_key, region)
        items: list[dict[str, Any]] = []
        page_token = None
        while True:
            kwargs = {}
            if page_token:
                kwargs["pageToken"] = page_token
            resp = client.get_bundles(**kwargs)
            for b in resp.get("bundles", []):
                items.append(
                    {
                        "bundle_id": b.get("bundleId"),
                        "name": b.get("name") or b.get("bundleId"),
                        "price": b.get("price"),
                        "cpu_count": b.get("cpuCount"),
                        "ram_size_in_gb": b.get("ramSizeInGb"),
                        "disk_size_in_gb": b.get("diskSizeInGb"),
                        "transfer_per_month_in_gb": b.get("transferPerMonthInGb"),
                        "power": b.get("power"),
                        "is_active": bool(b.get("isActive", True)),
                        "supported_platforms": b.get("supportedPlatforms") or [],
                    }
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return items
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)
        return []


def _paginate_instances(client) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = None
    while True:
        kwargs = {}
        if page_token:
            kwargs["pageToken"] = page_token
        resp = client.get_instances(**kwargs)
        items.extend(resp.get("instances", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def _paginate_static_ips(client) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = None
    while True:
        kwargs = {}
        if page_token:
            kwargs["pageToken"] = page_token
        resp = client.get_static_ips(**kwargs)
        items.extend(resp.get("staticIps", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def list_instances_in_region(
    access_key_id: str, secret_access_key: str, region: str
) -> list[dict[str, Any]]:
    try:
        client = make_client(access_key_id, secret_access_key, region)
        instances = _paginate_instances(client)
        static_ips = _paginate_static_ips(client)
        attached_map: dict[str, dict[str, Any]] = {}
        for sip in static_ips:
            if sip.get("isAttached") and sip.get("attachedTo"):
                attached_map[sip["attachedTo"]] = sip

        result = []
        for inst in instances:
            name = inst.get("name") or ""
            sip = attached_map.get(name)
            state = (inst.get("state") or {}).get("name") or "unknown"
            loc = inst.get("location") or {}
            created = inst.get("createdAt")
            if isinstance(created, datetime) and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            result.append(
                {
                    "name": name,
                    "region": loc.get("regionName") or region,
                    "availability_zone": loc.get("availabilityZone"),
                    "state": state,
                    "public_ip": inst.get("publicIpAddress"),
                    "private_ip": inst.get("privateIpAddress"),
                    "blueprint_id": inst.get("blueprintId"),
                    "blueprint_name": inst.get("blueprintName"),
                    "bundle_id": inst.get("bundleId"),
                    "is_static_ip": bool(inst.get("isStaticIp") or sip),
                    "static_ip_name": sip.get("name") if sip else None,
                    "created_at": created,
                }
            )
        return result
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)
        return []


def get_instance(
    access_key_id: str, secret_access_key: str, region: str, name: str
) -> dict[str, Any]:
    try:
        client = make_client(access_key_id, secret_access_key, region)
        inst = client.get_instance(instanceName=name).get("instance") or {}
        static_ips = _paginate_static_ips(client)
        sip = next(
            (s for s in static_ips if s.get("isAttached") and s.get("attachedTo") == name),
            None,
        )
        state = (inst.get("state") or {}).get("name") or "unknown"
        loc = inst.get("location") or {}
        created = inst.get("createdAt")
        if isinstance(created, datetime) and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return {
            "name": inst.get("name") or name,
            "region": loc.get("regionName") or region,
            "availability_zone": loc.get("availabilityZone"),
            "state": state,
            "public_ip": inst.get("publicIpAddress"),
            "private_ip": inst.get("privateIpAddress"),
            "blueprint_id": inst.get("blueprintId"),
            "blueprint_name": inst.get("blueprintName"),
            "bundle_id": inst.get("bundleId"),
            "is_static_ip": bool(inst.get("isStaticIp") or sip),
            "static_ip_name": sip.get("name") if sip else None,
            "created_at": created,
        }
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)
        return {}


def create_instance(
    access_key_id: str,
    secret_access_key: str,
    region: str,
    instance_name: str,
    blueprint_id: str,
    bundle_id: str,
    availability_zone: str | None = None,
    allocate_static_ip: bool = True,
) -> dict[str, Any]:
    try:
        client = make_client(access_key_id, secret_access_key, region)
        az = availability_zone
        if not az:
            regions = get_regions(access_key_id, secret_access_key)
            match = next((r for r in regions if r["name"] == region), None)
            azs = (match or {}).get("availability_zones") or []
            az = azs[0] if azs else f"{region}a"

        client.create_instances(
            instanceNames=[instance_name],
            availabilityZone=az,
            blueprintId=blueprint_id,
            bundleId=bundle_id,
        )

        static_ip_name = None
        if allocate_static_ip:
            # 等待实例进入非 pending 后再绑定静态 IP（超时仍会尝试绑定）
            _wait_instance_not_pending(client, instance_name, timeout=120)
            static_ip_name = f"{instance_name}-sip-{int(time.time())}"
            try:
                client.allocate_static_ip(staticIpName=static_ip_name)
                client.attach_static_ip(staticIpName=static_ip_name, instanceName=instance_name)
            except Exception:
                # 绑定失败时尽量释放，避免残留计费
                try:
                    client.release_static_ip(staticIpName=static_ip_name)
                except Exception:  # noqa: BLE001
                    logger.exception("释放孤立静态 IP 失败: %s", static_ip_name)
                static_ip_name = None
                # 实例已创建成功，静态 IP 失败不整体失败，提示用户可稍后换 IP
                return {
                    "name": instance_name,
                    "region": region,
                    "static_ip_name": None,
                    "message": "实例创建成功，但自动绑定静态 IP 失败，可稍后使用「换 IP」功能",
                }
        return {
            "name": instance_name,
            "region": region,
            "static_ip_name": static_ip_name,
            "message": "实例创建请求已提交" + ("，并已分配静态 IP" if static_ip_name else ""),
        }
    except LightsailError:
        raise
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)
        return {}


def _wait_instance_not_pending(client, name: str, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            inst = client.get_instance(instanceName=name).get("instance") or {}
            state = (inst.get("state") or {}).get("name") or ""
            if state and state.lower() not in {"pending", ""}:
                return
        except ClientError:
            pass
        time.sleep(3)


def start_instance(access_key_id: str, secret_access_key: str, region: str, name: str) -> None:
    try:
        client = make_client(access_key_id, secret_access_key, region)
        client.start_instance(instanceName=name)
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)


def stop_instance(access_key_id: str, secret_access_key: str, region: str, name: str) -> None:
    try:
        client = make_client(access_key_id, secret_access_key, region)
        client.stop_instance(instanceName=name)
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)


def reboot_instance(access_key_id: str, secret_access_key: str, region: str, name: str) -> None:
    try:
        client = make_client(access_key_id, secret_access_key, region)
        client.reboot_instance(instanceName=name)
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)


def change_static_ip(
    access_key_id: str, secret_access_key: str, region: str, name: str
) -> dict[str, Any]:
    """分配新静态 IP → detach 旧 → attach 新 → release 旧。"""
    client = make_client(access_key_id, secret_access_key, region)
    new_sip_name: str | None = None
    try:
        inst = client.get_instance(instanceName=name).get("instance") or {}
        state = ((inst.get("state") or {}).get("name") or "").lower()
        if state == "pending":
            raise LightsailError("实例处于 pending 状态，请稍后再试换 IP", code="Pending")

        old_ip = inst.get("publicIpAddress")
        static_ips = _paginate_static_ips(client)
        old_sip = next(
            (s for s in static_ips if s.get("isAttached") and s.get("attachedTo") == name),
            None,
        )
        old_sip_name = old_sip.get("name") if old_sip else None

        new_sip_name = f"{name}-sip-{int(time.time())}"
        client.allocate_static_ip(staticIpName=new_sip_name)

        if old_sip_name:
            client.detach_static_ip(staticIpName=old_sip_name)

        client.attach_static_ip(staticIpName=new_sip_name, instanceName=name)

        if old_sip_name:
            try:
                client.release_static_ip(staticIpName=old_sip_name)
            except Exception:  # noqa: BLE001
                logger.exception("释放旧静态 IP 失败: %s", old_sip_name)

        # 稍等再读新 IP
        time.sleep(2)
        try:
            refreshed = client.get_instance(instanceName=name).get("instance") or {}
            new_ip = refreshed.get("publicIpAddress")
        except Exception:  # noqa: BLE001
            new_ip = None

        return {
            "instance_name": name,
            "region": region,
            "old_ip": old_ip,
            "new_ip": new_ip,
            "static_ip_name": new_sip_name,
            "message": "已更换静态 IP",
        }
    except LightsailError:
        if new_sip_name:
            try:
                client.release_static_ip(staticIpName=new_sip_name)
            except Exception:  # noqa: BLE001
                logger.exception("回滚释放新静态 IP 失败: %s", new_sip_name)
        raise
    except Exception as exc:  # noqa: BLE001
        if new_sip_name:
            try:
                client.release_static_ip(staticIpName=new_sip_name)
            except Exception:  # noqa: BLE001
                logger.exception("回滚释放新静态 IP 失败: %s", new_sip_name)
        _raise_from_aws(exc)
        return {}


def delete_instance_and_static_ips(
    access_key_id: str, secret_access_key: str, region: str, name: str
) -> dict[str, Any]:
    """先记录关联静态 IP，删实例，再 release 静态 IP。"""
    try:
        client = make_client(access_key_id, secret_access_key, region)
        static_ips = _paginate_static_ips(client)
        related = [
            s.get("name")
            for s in static_ips
            if s.get("name")
            and (
                (s.get("isAttached") and s.get("attachedTo") == name)
                or (s.get("name") or "").startswith(f"{name}-sip-")
                or (s.get("name") or "").startswith(f"{name}-ip")
            )
        ]
        # 去重
        related = list(dict.fromkeys([n for n in related if n]))

        client.delete_instance(instanceName=name, forceDeleteAddOns=True)

        released: list[str] = []
        for sip_name in related:
            try:
                # 删除实例后通常已 detach，仍需 release 避免残留计费
                client.release_static_ip(staticIpName=sip_name)
                released.append(sip_name)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                # 已不存在则忽略
                if code not in {"NotFoundException", "InvalidInputException"}:
                    logger.warning("释放静态 IP %s 失败: %s", sip_name, exc)

        return {
            "name": name,
            "region": region,
            "released_static_ips": released,
            "message": f"实例已删除，释放静态 IP: {', '.join(released) if released else '无'}",
        }
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)
        return {}


def get_metric_sum_bytes(
    access_key_id: str,
    secret_access_key: str,
    region: str,
    name: str,
    metric_name: str,
    start: datetime,
    end: datetime,
    period: int = 86400,
) -> int:
    try:
        client = make_client(access_key_id, secret_access_key, region)
        resp = client.get_instance_metric_data(
            instanceName=name,
            metricName=metric_name,
            period=period,
            startTime=start,
            endTime=end,
            unit="Bytes",
            statistics=["Sum"],
        )
        total = 0.0
        for dp in resp.get("metricData", []) or []:
            if dp.get("sum") is not None:
                total += float(dp["sum"])
        return int(total)
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)
        return 0


def get_metric_series(
    access_key_id: str,
    secret_access_key: str,
    region: str,
    name: str,
    start: datetime,
    end: datetime,
    period: int,
) -> list[dict[str, Any]]:
    try:
        client = make_client(access_key_id, secret_access_key, region)

        def fetch(metric: str) -> dict[datetime, float]:
            resp = client.get_instance_metric_data(
                instanceName=name,
                metricName=metric,
                period=period,
                startTime=start,
                endTime=end,
                unit="Bytes",
                statistics=["Sum"],
            )
            out: dict[datetime, float] = {}
            for dp in resp.get("metricData", []) or []:
                ts = dp.get("timestamp")
                if ts is None:
                    continue
                if isinstance(ts, datetime) and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                out[ts] = float(dp.get("sum") or 0)
            return out

        inn = fetch("NetworkIn")
        outt = fetch("NetworkOut")
        keys = sorted(set(inn) | set(outt))
        return [
            {
                "timestamp": k,
                "network_in_bytes": inn.get(k, 0.0),
                "network_out_bytes": outt.get(k, 0.0),
            }
            for k in keys
        ]
    except Exception as exc:  # noqa: BLE001
        _raise_from_aws(exc)
        return []

"""Lightsail boto3 封装。所有调用使用调用方传入的用户密钥，不用环境默认凭证。"""

from __future__ import annotations

import base64
import logging
import re
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


def make_service_quotas_client(
    access_key_id: str, secret_access_key: str, region: str = "us-east-1"
):
    return boto3.client(
        "service-quotas",
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )


# Lightsail Service Quotas（按 Region 生效；Instances 配额单位是 vCPU）
# 文档: https://docs.aws.amazon.com/general/latest/gr/lightsail.html
LIGHTSAIL_QUOTA_INSTANCES_VCPU = "L-4259AF9B"
LIGHTSAIL_QUOTA_STATIC_IPS = "L-BBF0F260"


def format_vcpu_tier_label(vcpu: float | int | None) -> str | None:
    """将 vCPU 配额格式化为社区常用的 5V / 8V / 32V 标签。"""
    if vcpu is None:
        return None
    try:
        n = float(vcpu)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if abs(n - round(n)) < 1e-6:
        return f"{int(round(n))}V"
    return f"{n:g}V"


def get_lightsail_account_quotas(
    access_key_id: str,
    secret_access_key: str,
    region: str = "us-east-1",
    include_usage: bool = True,
) -> dict[str, Any]:
    """
    读取账号 Lightsail 配额（主要是每 Region 最大 vCPU，即常说的 5V/8V/32V）。

    使用 AWS Service Quotas API（非 Lightsail 自带 API）。
    部分新账号/受限账号可能查不到可调整配额，此时 vcpu_quota 为 null。
    """
    result: dict[str, Any] = {
        "region": region,
        "vcpu_quota": None,
        "vcpu_tier": None,
        "static_ip_quota": None,
        "used_vcpu": None,
        "used_instance_count": None,
        "remaining_vcpu": None,
        "quotas": [],
        "message": None,
        "error": None,
    }
    try:
        sq = make_service_quotas_client(access_key_id, secret_access_key, region)

        def _get_quota(code: str) -> float | None:
            try:
                resp = sq.get_service_quota(ServiceCode="lightsail", QuotaCode=code)
                q = resp.get("Quota") or {}
                val = q.get("Value")
                return float(val) if val is not None else None
            except ClientError as exc:
                code_name = exc.response.get("Error", {}).get("Code", "")
                # 配额未开通 / 无权限时降级为 list 扫描
                if code_name in {
                    "NoSuchResourceException",
                    "AccessDeniedException",
                    "AccessDenied",
                    "UnrecognizedClientException",
                }:
                    return None
                raise

        vcpu = _get_quota(LIGHTSAIL_QUOTA_INSTANCES_VCPU)
        static_ips = _get_quota(LIGHTSAIL_QUOTA_STATIC_IPS)

        # 若单项失败，尝试 list_service_quotas 兜底
        if vcpu is None or static_ips is None:
            try:
                page_token = None
                while True:
                    kwargs: dict[str, Any] = {"ServiceCode": "lightsail"}
                    if page_token:
                        kwargs["NextToken"] = page_token
                    resp = sq.list_service_quotas(**kwargs)
                    for q in resp.get("Quotas", []) or []:
                        result["quotas"].append(
                            {
                                "name": q.get("QuotaName"),
                                "code": q.get("QuotaCode"),
                                "value": q.get("Value"),
                                "adjustable": q.get("Adjustable"),
                                "unit": q.get("Unit"),
                            }
                        )
                        qc = q.get("QuotaCode")
                        if qc == LIGHTSAIL_QUOTA_INSTANCES_VCPU and vcpu is None and q.get("Value") is not None:
                            vcpu = float(q["Value"])
                        if qc == LIGHTSAIL_QUOTA_STATIC_IPS and static_ips is None and q.get("Value") is not None:
                            static_ips = float(q["Value"])
                    page_token = resp.get("NextToken")
                    if not page_token:
                        break
            except ClientError as exc:
                err = exc.response.get("Error", {})
                result["error"] = f"{err.get('Code')}: {err.get('Message')}"
                logger.warning("list_service_quotas 失败: %s", result["error"])

        result["vcpu_quota"] = vcpu
        result["vcpu_tier"] = format_vcpu_tier_label(vcpu)
        result["static_ip_quota"] = static_ips

        if vcpu is None and not result["error"]:
            result["message"] = (
                "未能读取 Lightsail Instances(vCPU) 配额，"
                "Service Quotas 可能对该账号显示为 Not available，需通过账单支持工单提额。"
            )
        elif vcpu is not None:
            result["message"] = (
                f"Lightsail 每 Region 实例配额约 {result['vcpu_tier']}（{int(vcpu) if vcpu == int(vcpu) else vcpu} vCPU）。"
                " 配额按 Region 计算；创建实例时按套餐 vCPU 占用。"
            )

        if include_usage:
            try:
                usage = _sum_vcpu_usage_in_region(access_key_id, secret_access_key, region)
                result["used_vcpu"] = usage["used_vcpu"]
                result["used_instance_count"] = usage["instance_count"]
                if vcpu is not None:
                    result["remaining_vcpu"] = max(0.0, float(vcpu) - float(usage["used_vcpu"]))
            except Exception as exc:  # noqa: BLE001
                logger.warning("统计 vCPU 用量失败: %s", exc)

        return result
    except Exception as exc:  # noqa: BLE001
        try:
            _raise_from_aws(exc)
        except LightsailError as le:
            result["error"] = le.message
            result["message"] = f"配额查询失败: {le.message}"
            return result
        result["error"] = str(exc)
        result["message"] = f"配额查询失败: {exc}"
        return result


def _sum_vcpu_usage_in_region(
    access_key_id: str, secret_access_key: str, region: str
) -> dict[str, Any]:
    """按当前 Region 已有实例估算占用的 vCPU（用 bundle 的 cpuCount）。"""
    client = make_client(access_key_id, secret_access_key, region)
    instances = _paginate_instances(client)
    # bundle_id -> cpu
    cpu_by_bundle: dict[str, int] = {}
    try:
        page_token = None
        while True:
            kwargs: dict[str, Any] = {}
            if page_token:
                kwargs["pageToken"] = page_token
            resp = client.get_bundles(**kwargs)
            for b in resp.get("bundles", []) or []:
                bid = b.get("bundleId")
                if bid and b.get("cpuCount") is not None:
                    cpu_by_bundle[bid] = int(b["cpuCount"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception:  # noqa: BLE001
        logger.exception("get_bundles 失败，用量按 1 vCPU/实例估算")

    used = 0
    for inst in instances:
        bid = inst.get("bundleId") or ""
        used += cpu_by_bundle.get(bid, 1)
    return {"used_vcpu": used, "instance_count": len(instances)}


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


def _is_windows_platform(platform: str | None, blueprint_id: str | None = None) -> bool:
    p = (platform or "").strip().upper()
    if p in {"WINDOWS", "WIN", "WINDOWS_SERVER"}:
        return True
    if p in {"LINUX", "LINUX_UNIX", "UNIX"}:
        return False
    bp = (blueprint_id or "").lower()
    return "windows" in bp or bp.startswith("win_")


def _validate_instance_password(password: str, *, windows: bool) -> None:
    if not password or len(password) < 8 or len(password) > 64:
        raise LightsailError("自定义密码长度需为 8-64 位", code="InvalidPassword")
    if password != password.strip() or re.search(r"[\x00-\x1f\x7f]", password):
        raise LightsailError("自定义密码不能包含首尾空格或控制字符", code="InvalidPassword")
    if windows and any(ch in password for ch in ['"', "'", "`"]):
        raise LightsailError("Windows 密码请勿包含引号或反引号", code="InvalidPassword")


def build_password_user_data(
    password: str,
    platform: str | None = None,
    blueprint_id: str | None = None,
) -> str:
    """生成创建实例时注入的 userData，用于设置自定义登录密码。"""
    windows = _is_windows_platform(platform, blueprint_id)
    _validate_instance_password(password, windows=windows)
    if windows:
        # Lightsail Windows userData 为 PowerShell；单引号字符串内把 ' 变成 ''
        escaped = password.replace("'", "''")
        return (
            f"net user Administrator '{escaped}'\n"
            "try {\n"
            "  $u = Get-LocalUser -Name 'Administrator' -ErrorAction SilentlyContinue\n"
            "  if ($u -and -not $u.Enabled) { Enable-LocalUser -Name 'Administrator' }\n"
            "} catch {}\n"
        )

    # Linux：base64 传参，避免 shell 转义问题；为常见默认用户与 root 设置密码并开启 SSH 密码登录
    b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
    return (
        "#!/bin/bash\n"
        "set +e\n"
        f"PASS=$(printf '%s' '{b64}' | base64 -d 2>/dev/null || echo '{b64}' | base64 -d)\n"
        'if [ -z "$PASS" ]; then\n'
        "  exit 0\n"
        "fi\n"
        "for u in ubuntu admin debian ec2-user bitnami centos rocky almalinux fedora; do\n"
        '  if id "$u" >/dev/null 2>&1; then\n'
        '    echo "$u:$PASS" | chpasswd\n'
        "  fi\n"
        "done\n"
        'echo "root:$PASS" | chpasswd\n'
        "if [ -d /etc/ssh/sshd_config.d ]; then\n"
        "  cat > /etc/ssh/sshd_config.d/99-custom-password-auth.conf <<'EOF'\n"
        "PasswordAuthentication yes\n"
        "PermitRootLogin yes\n"
        "KbdInteractiveAuthentication yes\n"
        "EOF\n"
        "fi\n"
        "if [ -f /etc/ssh/sshd_config ]; then\n"
        "  sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config\n"
        "  sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config\n"
        "  sed -i 's/^#\\?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication yes/' /etc/ssh/sshd_config\n"
        "  sed -i 's/^#\\?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication yes/' /etc/ssh/sshd_config\n"
        "fi\n"
        "systemctl restart sshd 2>/dev/null || systemctl restart ssh 2>/dev/null || service ssh restart 2>/dev/null || service sshd restart 2>/dev/null || true\n"
    )


def open_all_instance_ports(client, instance_name: str) -> None:
    """
    将实例公网防火墙设为：
    - 所有协议 / 0-65535 / 任意 IPv4 + IPv6
    - 保留 Lightsail 浏览器 SSH(22) 与浏览器 RDP(3389)
    """
    port_infos = [
        {
            "fromPort": 0,
            "toPort": 65535,
            "protocol": "all",
            "cidrs": ["0.0.0.0/0"],
            "ipv6Cidrs": ["::/0"],
        },
        {
            "fromPort": 22,
            "toPort": 22,
            "protocol": "tcp",
            "cidrListAliases": ["lightsail-connect"],
        },
        {
            "fromPort": 3389,
            "toPort": 3389,
            "protocol": "tcp",
            "cidrListAliases": ["lightsail-connect"],
        },
    ]
    try:
        client.put_instance_public_ports(portInfos=port_infos, instanceName=instance_name)
    except ClientError:
        # 部分账号/镜像对 lightsail-connect 条目敏感时，降级为仅全端口开放
        client.put_instance_public_ports(
            portInfos=[
                {
                    "fromPort": 0,
                    "toPort": 65535,
                    "protocol": "all",
                    "cidrs": ["0.0.0.0/0"],
                    "ipv6Cidrs": ["::/0"],
                }
            ],
            instanceName=instance_name,
        )


def create_instance(
    access_key_id: str,
    secret_access_key: str,
    region: str,
    instance_name: str,
    blueprint_id: str,
    bundle_id: str,
    availability_zone: str | None = None,
    allocate_static_ip: bool = True,
    password: str | None = None,
    platform: str | None = None,
    open_all_ports: bool = True,
) -> dict[str, Any]:
    try:
        client = make_client(access_key_id, secret_access_key, region)
        az = availability_zone
        if not az:
            regions = get_regions(access_key_id, secret_access_key)
            match = next((r for r in regions if r["name"] == region), None)
            azs = (match or {}).get("availability_zones") or []
            az = azs[0] if azs else f"{region}a"

        create_kwargs: dict[str, Any] = {
            "instanceNames": [instance_name],
            "availabilityZone": az,
            "blueprintId": blueprint_id,
            "bundleId": bundle_id,
        }
        if password:
            create_kwargs["userData"] = build_password_user_data(
                password, platform=platform, blueprint_id=blueprint_id
            )

        client.create_instances(**create_kwargs)

        # 等待实例离开 pending，再配置防火墙 / 静态 IP
        _wait_instance_not_pending(client, instance_name, timeout=120)

        firewall_opened = False
        firewall_error: str | None = None
        if open_all_ports:
            try:
                open_all_instance_ports(client, instance_name)
                firewall_opened = True
            except Exception as exc:  # noqa: BLE001
                # 实例已创建成功，防火墙失败不整体失败
                firewall_error = str(exc)
                logger.exception("开放全部端口失败: %s", instance_name)

        static_ip_name = None
        static_ip_error = False
        if allocate_static_ip:
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
                static_ip_error = True

        parts = ["实例创建请求已提交"]
        if firewall_opened:
            parts.append("已开放全部防火墙端口(0-65535)")
        elif open_all_ports and firewall_error:
            parts.append("开放全部端口失败，可稍后在控制台手动放行")
        if static_ip_name:
            parts.append("并已分配静态 IP")
        elif allocate_static_ip and static_ip_error:
            parts.append("自动绑定静态 IP 失败，可稍后使用「换 IP」功能")
        if password:
            parts.append("已注入自定义密码（首次启动后生效）")

        return {
            "name": instance_name,
            "region": region,
            "static_ip_name": static_ip_name,
            "message": "，".join(parts) if len(parts) == 1 else parts[0] + "，" + "，".join(parts[1:]),
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

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_admin: bool
    monthly_limit_gb: float | None = None
    auto_stop_on_limit_default: bool = False
    created_at: datetime
    has_credentials: bool = False
    credential_count: int = 0


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6)
    is_admin: bool = False
    monthly_limit_gb: float | None = None
    auto_stop_on_limit_default: bool = False


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=6)
    is_admin: bool | None = None
    monthly_limit_gb: float | None = None
    auto_stop_on_limit_default: bool | None = None


class CredentialCreate(BaseModel):
    access_key_id: str = Field(min_length=16, max_length=128)
    secret_access_key: str = Field(min_length=16, max_length=128)
    account_label: str | None = None
    is_default: bool = False


class CredentialUpdate(BaseModel):
    access_key_id: str | None = Field(default=None, min_length=16, max_length=128)
    secret_access_key: str | None = Field(default=None, min_length=16, max_length=128)
    account_label: str | None = None
    is_default: bool | None = None


class CredentialItem(BaseModel):
    id: int
    access_key_masked: str
    account_label: str | None = None
    is_default: bool = False
    last_validated_at: datetime | None = None
    created_at: datetime | None = None
    # Lightsail 配额（vCPU/Region，如 5V/8V/32V）
    vcpu_quota: float | None = None
    vcpu_tier: str | None = None
    static_ip_quota: float | None = None
    used_vcpu: float | None = None
    used_instance_count: int | None = None
    remaining_vcpu: float | None = None
    quota_region: str | None = None
    quota_message: str | None = None
    quota_checked_at: datetime | None = None


class CredentialListOut(BaseModel):
    items: list[CredentialItem]
    has_credentials: bool


# 兼容旧前端字段（单对象），同时提供列表
class CredentialOut(BaseModel):
    has_credentials: bool
    items: list[CredentialItem] = []
    # 默认凭证摘要
    id: int | None = None
    access_key_masked: str | None = None
    account_label: str | None = None
    last_validated_at: datetime | None = None


class InstanceCreate(BaseModel):
    region: str
    instance_name: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9\-]*$")
    blueprint_id: str
    bundle_id: str
    allocate_static_ip: bool = True
    availability_zone: str | None = None
    credential_id: int | None = None
    # 自定义登录密码（通过 userData 注入；不落库）
    password: str | None = Field(default=None, min_length=8, max_length=64)
    # 可选：LINUX_UNIX / WINDOWS，用于选择注入脚本
    platform: str | None = None
    # 创建后开放全部防火墙端口（0-65535 / all / 任意 IPv4+IPv6）
    open_all_ports: bool = True


class InstanceSettingsUpdate(BaseModel):
    monthly_limit_gb: float | None = None
    auto_stop_on_limit: bool | None = None
    note: str | None = None


class InstanceTraffic(BaseModel):
    in_gb: float = 0
    out_gb: float = 0
    total_gb: float = 0
    limit_gb: float | None = None
    over_limit: bool = False
    year_month: str | None = None


class InstanceOut(BaseModel):
    name: str
    region: str
    availability_zone: str | None = None
    state: str
    public_ip: str | None = None
    private_ip: str | None = None
    blueprint_id: str | None = None
    blueprint_name: str | None = None
    bundle_id: str | None = None
    is_static_ip: bool = False
    static_ip_name: str | None = None
    created_at: datetime | None = None
    traffic: InstanceTraffic | None = None
    monthly_limit_gb: float | None = None
    auto_stop_on_limit: bool = False
    note: str | None = None
    credential_id: int | None = None
    account_label: str | None = None


class CreateInstanceResponse(BaseModel):
    name: str
    region: str
    credential_id: int | None = None
    static_ip_name: str | None = None
    message: str


class ChangeIpResponse(BaseModel):
    instance_name: str
    region: str
    old_ip: str | None = None
    new_ip: str | None = None
    static_ip_name: str
    message: str


class TrafficInstanceRow(BaseModel):
    region: str
    name: str
    credential_id: int | None = None
    account_label: str | None = None
    in_gb: float
    out_gb: float
    total_gb: float
    limit_gb: float | None = None
    over_limit: bool = False
    auto_stop_on_limit: bool = False
    year_month: str


class TrafficRegionRow(BaseModel):
    region: str
    total_gb: float
    instance_count: int


class TrafficSummary(BaseModel):
    year_month: str
    instances: list[TrafficInstanceRow]
    by_region: list[TrafficRegionRow]
    note: str = "流量基于 Lightsail NetworkIn/NetworkOut 指标估算，与账单可能存在差异"


class MetricPoint(BaseModel):
    timestamp: datetime
    network_in_bytes: float
    network_out_bytes: float


class MetricsSeries(BaseModel):
    region: str
    name: str
    period: str
    points: list[MetricPoint]


class RegionOut(BaseModel):
    name: str
    display_name: str
    continent_code: str | None = None


class BundleOut(BaseModel):
    bundle_id: str
    name: str
    price: float | None = None
    cpu_count: int | None = None
    ram_size_in_gb: float | None = None
    disk_size_in_gb: int | None = None
    transfer_per_month_in_gb: int | None = None
    power: int | None = None
    is_active: bool = True
    supported_platforms: list[str] = []


class BlueprintOut(BaseModel):
    blueprint_id: str
    name: str
    group: str | None = None
    type: str | None = None
    platform: str | None = None
    version: str | None = None
    is_active: bool = True


class MessageOut(BaseModel):
    message: str


class OperationResult(BaseModel):
    message: str
    region: str
    name: str


TokenResponse.model_rebuild()

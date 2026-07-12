from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    monthly_limit_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 用户级默认：新建实例设置时的默认勾选，不强制所有实例
    auto_stop_on_limit_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    credentials: Mapped[list["AwsCredential"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    instance_settings: Mapped[list["InstanceSetting"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    traffic_usages: Mapped[list["TrafficUsage"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    operation_logs: Mapped[list["OperationLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials)

    @property
    def default_credential(self) -> "AwsCredential | None":
        if not self.credentials:
            return None
        for c in self.credentials:
            if c.is_default:
                return c
        return self.credentials[0]


class AwsCredential(Base):
    __tablename__ = "aws_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    access_key_id_enc: Mapped[str] = mapped_column(Text, nullable=False)
    secret_access_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    account_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="credentials")
    instance_settings: Mapped[list["InstanceSetting"]] = relationship(back_populates="credential")
    traffic_usages: Mapped[list["TrafficUsage"]] = relationship(back_populates="credential")


class InstanceSetting(Base):
    __tablename__ = "instance_settings"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "credential_id", "region", "instance_name", name="uq_instance_setting"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("aws_credentials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    region: Mapped[str] = mapped_column(String(32), nullable=False)
    instance_name: Mapped[str] = mapped_column(String(128), nullable=False)
    monthly_limit_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 勾选后超限才自动关机；默认 False 仅告警
    auto_stop_on_limit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="instance_settings")
    credential: Mapped[AwsCredential] = relationship(back_populates="instance_settings")


class TrafficUsage(Base):
    __tablename__ = "traffic_usage"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "credential_id",
            "region",
            "instance_name",
            "year_month",
            name="uq_traffic_usage",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("aws_credentials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    region: Mapped[str] = mapped_column(String(32), nullable=False)
    instance_name: Mapped[str] = mapped_column(String(128), nullable=False)
    year_month: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    network_in_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    network_out_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="traffic_usages")
    credential: Mapped[AwsCredential] = relationship(back_populates="traffic_usages")


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="success")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="operation_logs")

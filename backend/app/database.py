from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _sqlite_columns(table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _add_column_if_missing(table: str, column_def: str) -> None:
    col_name = column_def.split()[0]
    cols = _sqlite_columns(table)
    if not cols or col_name in cols:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_def}"))


def migrate_schema() -> None:
    """轻量 SQLite 迁移：补列；多 Key 结构变更时重建相关表（开发期可接受）。"""
    if not settings.database_url.startswith("sqlite"):
        return

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    if not tables:
        return

    # users 新列
    if "users" in tables:
        _add_column_if_missing("users", "auto_stop_on_limit_default BOOLEAN DEFAULT 0 NOT NULL")

    # aws_credentials：若仍有 user_id 唯一约束（旧版一对一），重建为多对一
    if "aws_credentials" in tables:
        cols = _sqlite_columns("aws_credentials")
        _add_column_if_missing("aws_credentials", "is_default BOOLEAN DEFAULT 0 NOT NULL")
        _add_column_if_missing("aws_credentials", "vcpu_quota FLOAT")
        _add_column_if_missing("aws_credentials", "vcpu_tier VARCHAR(32)")
        _add_column_if_missing("aws_credentials", "static_ip_quota FLOAT")
        _add_column_if_missing("aws_credentials", "used_vcpu FLOAT")
        _add_column_if_missing("aws_credentials", "used_instance_count INTEGER")
        _add_column_if_missing("aws_credentials", "quota_region VARCHAR(32)")
        _add_column_if_missing("aws_credentials", "quota_message TEXT")
        _add_column_if_missing("aws_credentials", "quota_checked_at DATETIME")
        # 检测旧 unique(user_id)：通过索引名粗判，或直接尝试插入逻辑依赖 is_default
        # 若缺少多 Key 支持所需结构，重建表并迁移数据
        with engine.begin() as conn:
            idx_rows = conn.execute(text("PRAGMA index_list('aws_credentials')")).fetchall()
            # SQLite unique constraint on user_id creates an index; recreate if unique index on user_id only
            need_rebuild = False
            for row in idx_rows:
                # row: (seq, name, unique, origin, partial)
                if row[2]:  # unique
                    info = conn.execute(text(f"PRAGMA index_info('{row[1]}')")).fetchall()
                    col_ids = [r[2] for r in info]
                    if col_ids == ["user_id"]:
                        need_rebuild = True
                        break
            if need_rebuild:
                conn.execute(text("ALTER TABLE aws_credentials RENAME TO aws_credentials_old"))
                conn.execute(
                    text(
                        """
                        CREATE TABLE aws_credentials (
                            id INTEGER NOT NULL PRIMARY KEY,
                            user_id INTEGER NOT NULL,
                            access_key_id_enc TEXT NOT NULL,
                            secret_access_key_enc TEXT NOT NULL,
                            account_label VARCHAR(128),
                            is_default BOOLEAN DEFAULT 0 NOT NULL,
                            last_validated_at DATETIME,
                            created_at DATETIME,
                            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
                        )
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO aws_credentials
                        (id, user_id, access_key_id_enc, secret_access_key_enc, account_label,
                         is_default, last_validated_at, created_at)
                        SELECT id, user_id, access_key_id_enc, secret_access_key_enc, account_label,
                               1, last_validated_at, created_at
                        FROM aws_credentials_old
                        """
                    )
                )
                conn.execute(text("DROP TABLE aws_credentials_old"))
                conn.execute(text("CREATE INDEX ix_aws_credentials_user_id ON aws_credentials (user_id)"))

    # instance_settings / traffic_usage 增加 credential_id、auto_stop
    if "instance_settings" in tables:
        cols = _sqlite_columns("instance_settings")
        if "credential_id" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE instance_settings RENAME TO instance_settings_old"))
                conn.execute(
                    text(
                        """
                        CREATE TABLE instance_settings (
                            id INTEGER NOT NULL PRIMARY KEY,
                            user_id INTEGER NOT NULL,
                            credential_id INTEGER NOT NULL,
                            region VARCHAR(32) NOT NULL,
                            instance_name VARCHAR(128) NOT NULL,
                            monthly_limit_gb FLOAT,
                            auto_stop_on_limit BOOLEAN DEFAULT 0 NOT NULL,
                            note VARCHAR(255),
                            created_at DATETIME,
                            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
                            FOREIGN KEY(credential_id) REFERENCES aws_credentials (id) ON DELETE CASCADE,
                            UNIQUE (user_id, credential_id, region, instance_name)
                        )
                        """
                    )
                )
                # 旧数据：绑定到该用户默认/第一组凭证
                conn.execute(
                    text(
                        """
                        INSERT INTO instance_settings
                        (id, user_id, credential_id, region, instance_name, monthly_limit_gb,
                         auto_stop_on_limit, note, created_at)
                        SELECT o.id, o.user_id,
                               (SELECT c.id FROM aws_credentials c WHERE c.user_id = o.user_id
                                ORDER BY c.is_default DESC, c.id ASC LIMIT 1),
                               o.region, o.instance_name, o.monthly_limit_gb, 0, o.note, o.created_at
                        FROM instance_settings_old o
                        WHERE EXISTS (
                            SELECT 1 FROM aws_credentials c WHERE c.user_id = o.user_id
                        )
                        """
                    )
                )
                conn.execute(text("DROP TABLE instance_settings_old"))
        else:
            _add_column_if_missing(
                "instance_settings", "auto_stop_on_limit BOOLEAN DEFAULT 0 NOT NULL"
            )

    if "traffic_usage" in tables:
        cols = _sqlite_columns("traffic_usage")
        if "credential_id" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE traffic_usage RENAME TO traffic_usage_old"))
                conn.execute(
                    text(
                        """
                        CREATE TABLE traffic_usage (
                            id INTEGER NOT NULL PRIMARY KEY,
                            user_id INTEGER NOT NULL,
                            credential_id INTEGER NOT NULL,
                            region VARCHAR(32) NOT NULL,
                            instance_name VARCHAR(128) NOT NULL,
                            year_month VARCHAR(7) NOT NULL,
                            network_in_bytes BIGINT DEFAULT 0 NOT NULL,
                            network_out_bytes BIGINT DEFAULT 0 NOT NULL,
                            last_synced_at DATETIME,
                            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
                            FOREIGN KEY(credential_id) REFERENCES aws_credentials (id) ON DELETE CASCADE,
                            UNIQUE (user_id, credential_id, region, instance_name, year_month)
                        )
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO traffic_usage
                        (id, user_id, credential_id, region, instance_name, year_month,
                         network_in_bytes, network_out_bytes, last_synced_at)
                        SELECT o.id, o.user_id,
                               (SELECT c.id FROM aws_credentials c WHERE c.user_id = o.user_id
                                ORDER BY c.is_default DESC, c.id ASC LIMIT 1),
                               o.region, o.instance_name, o.year_month,
                               o.network_in_bytes, o.network_out_bytes, o.last_synced_at
                        FROM traffic_usage_old o
                        WHERE EXISTS (
                            SELECT 1 FROM aws_credentials c WHERE c.user_id = o.user_id
                        )
                        """
                    )
                )
                conn.execute(text("DROP TABLE traffic_usage_old"))


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    migrate_schema()
    # 迁移后可能新建表，再 create_all 一次补齐
    Base.metadata.create_all(bind=engine)

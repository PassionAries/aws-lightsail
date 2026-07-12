from app.services.traffic import bytes_to_gb, build_traffic_summary
from app.models import AwsCredential, TrafficUsage, User
from app.database import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_region_traffic_sum():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    user = User(username="t", password_hash="x", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)

    cred = AwsCredential(
        user_id=user.id,
        access_key_id_enc="enc-ak",
        secret_access_key_enc="enc-sk",
        account_label="main",
        is_default=True,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    db.refresh(user)

    gb = 1024 * (1024**3)  # 1024 GiB in bytes
    for name in ("a", "b"):
        db.add(
            TrafficUsage(
                user_id=user.id,
                credential_id=cred.id,
                region="ap-northeast-1",
                instance_name=name,
                year_month="2099-01",
                network_in_bytes=gb // 2,
                network_out_bytes=gb // 2,
            )
        )
    db.commit()

    import app.services.traffic as traffic_mod

    original = traffic_mod.current_year_month
    traffic_mod.current_year_month = lambda now=None: "2099-01"
    try:
        summary = build_traffic_summary(db, user)
    finally:
        traffic_mod.current_year_month = original

    assert len(summary["instances"]) == 2
    assert summary["instances"][0]["total_gb"] == 1024.0
    region = next(r for r in summary["by_region"] if r["region"] == "ap-northeast-1")
    assert region["total_gb"] == 2048.0
    assert region["instance_count"] == 2
    assert bytes_to_gb(1024**3) == 1.0
    print("test_region_traffic_sum OK")


if __name__ == "__main__":
    test_region_traffic_sum()

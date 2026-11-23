"""超阈探头在线单轨口径：三失败测。

甲：偏移超阈探头对外不得亮在线，标在线被拒且正式字段零改，台账当天封口。
乙：未转正的影子草案不得在任何一页亮绿灯，五页在线标识同源同值。
争用：两路会话同探头抢标在线，后到争用见红，业务零写。
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db

BARRED = "超阈禁线"


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user="admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _probe(c: TestClient, h: dict, code: str) -> dict:
    items = c.get("/api/probes", headers=h).json()["items"]
    return next(p for p in items if p["probe_code"] == code)


def _raw_online(probe_id: int) -> str:
    with connect() as conn:
        return conn.execute(
            "SELECT online_status FROM probes WHERE id=?", (probe_id,)
        ).fetchone()[0]


def test_over_threshold_barred_and_mark_online_rejected():
    """失败测甲：超阈仍在线即假绿；拒标在线且正式字段零改，台账当天封口。"""
    c = TestClient(app)
    h = _auth(c)
    # 假绿夹具：PR-HD-A2 正式字段在线，最新校准偏移 +1.2℃ 超阈
    fake_green = _probe(c, h, "PR-HD-A2")
    assert fake_green["online_status"] != "在线", "超阈探头对外不得亮在线"
    # 批次页挂接必须被拦：超阈探头禁止写批次
    r = c.post(
        "/api/batches",
        headers=h,
        json={
            "chamber_id": fake_green["chamber_id"],
            "probe_id": fake_green["id"],
            "cargo_ref": "超阈探头挂批",
            "temp_points": [],
        },
    )
    assert r.status_code == 409, "超阈探头挂批须被拒"
    # 离线+超阈夹具：PR-HB-B2 标在线须被拒，正式字段零改
    offline = _probe(c, h, "PR-HB-B2")
    r2 = c.post(f"/api/probes/{offline['id']}/online", headers=h)
    assert r2.status_code == 409, "超阈探头标在线须被拒"
    assert "超阈" in r2.json()["detail"]
    assert _raw_online(offline["id"]) == "离线", "拒标后正式字段须零改"
    # 班组记入偏移在线台账，当天封口
    ledger = c.get("/api/offset-online-ledger", headers=h).json()["items"]
    entry = next(
        x for x in ledger if x["probe_code"] == "PR-HB-B2" and x["action"] == "拒标在线"
    )
    assert "偏移偏航" in entry["detail"]
    assert entry["shift_date"] == date.today().isoformat()
    assert entry["sealed_on"] == date.today().isoformat()


def test_shadow_draft_no_green_on_any_page():
    """失败测乙：影子在线草案未转正，五页任何一路都不得亮绿灯。"""
    c = TestClient(app)
    h = _auth(c)
    probe = _probe(c, h, "PR-HD-A2")
    pid, cid = probe["id"], probe["chamber_id"]
    # 探头页 / 探头详情
    assert probe["online_status"] == BARRED
    detail = c.get(f"/api/probes/{pid}", headers=h).json()["probe"]
    assert detail["online_status"] == BARRED
    # 校准页：同行探头在线标识同源
    cals = c.get("/api/calibrations", headers=h).json()["items"]
    cal = next(x for x in cals if x["probe_code"] == "PR-HD-A2")
    assert cal["probe_online"] == BARRED
    assert cal["over_threshold"] is True
    # 批次页：该探头历史批次行同源
    batches = c.get("/api/batches", headers=h).json()["items"]
    batch = next(x for x in batches if x["probe_code"] == "PR-HD-A2")
    assert batch["probe_online"] == BARRED
    # 对账页：仓间内探头在线态同源
    recon = c.get("/api/reconcile/CK-HD-01", headers=h).json()
    rp = next(x for x in recon["probes"] if x["probe_code"] == "PR-HD-A2")
    assert rp["online_status"] == BARRED
    # 仓间页：仓间详情探头表同源
    chamber = c.get(f"/api/chambers/{cid}", headers=h).json()
    cp = next(x for x in chamber["probes"] if x["probe_code"] == "PR-HD-A2")
    assert cp["online_status"] == BARRED


def test_two_sessions_contend_mark_online():
    """争用失败测：两路会话同探头抢标在线，须占探头锁，后到见红零写。"""
    c = TestClient(app)
    h1 = _auth(c, "admin")
    h2 = _auth(c, "ops")
    probe = _probe(c, h1, "PR-HB-B2")
    pid = probe["id"]
    lock_key = f"probe:{pid}"
    # 会话一占住探头锁（在途标在线）
    assert (
        c.post("/api/locks/acquire", headers=h1, json={"lock_key": lock_key}).status_code
        == 200
    )
    # 会话二抢标同一探头 → 争用见红，正式字段零改
    r = c.post(f"/api/probes/{pid}/online", headers=h2)
    assert r.status_code == 409
    assert "争用" in r.json()["detail"]
    assert _raw_online(pid) == "离线"
    # 会话一放锁，会话二再抢 → 超阈拒标（非争用），正式字段仍零改
    assert (
        c.post("/api/locks/release", headers=h1, json={"lock_key": lock_key}).status_code
        == 200
    )
    r2 = c.post(f"/api/probes/{pid}/online", headers=h2)
    assert r2.status_code == 409
    assert "超阈" in r2.json()["detail"]
    assert _raw_online(pid) == "离线"

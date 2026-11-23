"""探头校准记录 + 偏移在线台账。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit, write_offset_ledger
from models import CalibrationIn
from validators import (
    OFFSET_LIMIT_C,
    effective_online_status,
    latest_calibration,
    latest_calibrations_map,
    offset_over_threshold,
)

router = APIRouter(tags=["calibration"])


@router.get("/api/calibrations")
def list_calibrations(authorization: AuthHeader = None, probe_id: int | None = None):
    require_user(authorization)
    with connect() as conn:
        base = """SELECT c.*, p.probe_code, p.online_status AS probe_status
                  FROM calibration_records c JOIN probes p ON p.id=c.probe_id"""
        if probe_id:
            rows = conn.execute(
                base + " WHERE c.probe_id=? ORDER BY c.id DESC", (probe_id,)
            ).fetchall()
        else:
            rows = conn.execute(base + " ORDER BY c.id DESC").fetchall()
        cal_map = latest_calibrations_map(conn)
        items = []
        for r in rows:
            d = dict(r)
            d.pop("probe_status", None)  # 单轨：原始正式字段不对外，只给派生值
            d["over_threshold"] = offset_over_threshold(d["offset_c"])
            d["probe_online"] = effective_online_status(
                r["probe_status"], cal_map.get(r["probe_id"])
            )
            items.append(d)
        return {"items": items, "offset_limit_c": OFFSET_LIMIT_C}


@router.get("/api/offset-online-ledger")
def list_offset_online_ledger(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM offset_online_ledger ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/calibrations")
def add_calibration(body: CalibrationIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        probe = conn.execute(
            "SELECT * FROM probes WHERE id=?", (body.probe_id,)
        ).fetchone()
        if not probe:
            raise HTTPException(404, "探头不存在")
        prev = latest_calibration(conn, body.probe_id)
        over = offset_over_threshold(body.offset_c)
        status = "超阈" if over else "有效"
        cur = conn.execute(
            """INSERT INTO calibration_records(probe_id,calibrated_at,offset_c,
               valid_until,officer,status) VALUES (?,?,?,?,?,?)""",
            (
                body.probe_id,
                now_iso(),
                body.offset_c,
                body.valid_until,
                body.officer,
                status,
            ),
        )
        conn.execute(
            "UPDATE probes SET calibration_due=? WHERE id=?",
            (body.valid_until, body.probe_id),
        )
        if over:
            write_offset_ledger(
                conn,
                body.probe_id,
                probe["probe_code"],
                "偏移偏航",
                body.offset_c,
                user,
                f"校准偏移 {body.offset_c}℃ 超阈 ±{OFFSET_LIMIT_C}℃，探头对外禁标在线",
            )
        elif prev is not None and offset_over_threshold(prev["offset_c"]):
            write_offset_ledger(
                conn,
                body.probe_id,
                probe["probe_code"],
                "复校归阈",
                body.offset_c,
                user,
                "复校回阈，探头恢复可标在线",
            )
        write_audit(
            conn,
            user,
            "calibration_add",
            probe["probe_code"],
            {"cal_id": cur.lastrowid, "valid_until": body.valid_until},
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "probe_code": probe["probe_code"],
            "valid_until": body.valid_until,
            "over_threshold": over,
            "status": status,
        }

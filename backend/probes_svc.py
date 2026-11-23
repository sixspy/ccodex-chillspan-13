"""探头登记与在线态。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit, write_offset_ledger
from locks import release, require_lock
from models import ProbeIn
from validators import (
    OFFSET_LIMIT_C,
    effective_online_status,
    latest_calibration,
    latest_calibrations_map,
    offset_over_threshold,
)

router = APIRouter(tags=["probes"])


@router.get("/api/probes")
def list_probes(authorization: AuthHeader = None, chamber_id: int | None = None):
    require_user(authorization)
    with connect() as conn:
        if chamber_id:
            rows = conn.execute(
                """SELECT p.*, c.chamber_code, c.region FROM probes p
                   JOIN cold_chambers c ON c.id=p.chamber_id
                   WHERE p.chamber_id=? ORDER BY p.id""",
                (chamber_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT p.*, c.chamber_code, c.region FROM probes p
                   JOIN cold_chambers c ON c.id=p.chamber_id ORDER BY p.id"""
            ).fetchall()
        cal_map = latest_calibrations_map(conn)
        items = []
        for r in rows:
            d = dict(r)
            d["online_status"] = effective_online_status(
                r["online_status"], cal_map.get(r["id"])
            )
            items.append(d)
        return {"items": items}


@router.get("/api/probes/{probe_id}")
def get_probe(probe_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            """SELECT p.*, c.name chamber_name, c.target_min, c.target_max
               FROM probes p JOIN cold_chambers c ON c.id=p.chamber_id
               WHERE p.id=?""",
            (probe_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "探头不存在")
        cal = latest_calibration(conn, probe_id)
        probe = dict(row)
        probe["online_status"] = effective_online_status(row["online_status"], cal)
        cals = conn.execute(
            "SELECT * FROM calibration_records WHERE probe_id=? ORDER BY id DESC",
            (probe_id,),
        ).fetchall()
        cal_items = []
        for x in cals:
            d = dict(x)
            d["over_threshold"] = offset_over_threshold(d["offset_c"])
            cal_items.append(d)
        return {"probe": probe, "calibrations": cal_items}


@router.post("/api/probes")
def register_probe(body: ProbeIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        ch = conn.execute(
            "SELECT id FROM cold_chambers WHERE id=?", (body.chamber_id,)
        ).fetchone()
        if not ch:
            raise HTTPException(404, "仓间不存在")
        if conn.execute(
            "SELECT id FROM probes WHERE probe_code=?", (body.probe_code,)
        ).fetchone():
            raise HTTPException(409, "探头编码已存在")
        cur = conn.execute(
            """INSERT INTO probes(probe_code,chamber_id,model,online_status,
               calibration_due,created_at) VALUES (?,?,?,?,?,?)""",
            (
                body.probe_code,
                body.chamber_id,
                body.model,
                "在线",
                body.calibration_due,
                now_iso(),
            ),
        )
        write_audit(conn, user, "probe_register", body.probe_code, {"id": cur.lastrowid})
        conn.commit()
        return {"id": cur.lastrowid, "probe_code": body.probe_code, "online_status": "在线"}


@router.post("/api/probes/{probe_id}/online")
def set_online(probe_id: int, authorization: AuthHeader = None):
    """标在线：占探头锁；偏移超阈则正式字段零改、记台账并拒标。"""
    user = require_writer(authorization)
    lock_key = f"probe:{probe_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            row = conn.execute(
                "SELECT * FROM probes WHERE id=?", (probe_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "探头不存在")
            cal = latest_calibration(conn, probe_id)
            if cal is not None and offset_over_threshold(cal["offset_c"]):
                write_offset_ledger(
                    conn,
                    probe_id,
                    row["probe_code"],
                    "拒标在线",
                    cal["offset_c"],
                    user,
                    f"偏移偏航 {cal['offset_c']}℃ 超阈 ±{OFFSET_LIMIT_C}℃，拒标在线",
                )
                write_audit(
                    conn,
                    user,
                    "probe_online_rejected",
                    row["probe_code"],
                    {"offset_c": cal["offset_c"]},
                )
                conn.commit()
                release(conn, lock_key, user)
                conn.commit()
                raise HTTPException(409, "探头校准偏移超阈，拒标在线")
            conn.execute(
                "UPDATE probes SET online_status='在线' WHERE id=?", (probe_id,)
            )
            write_audit(conn, user, "probe_online", row["probe_code"], {})
            conn.commit()
            return {"id": probe_id, "online_status": "在线"}
        finally:
            release(conn, lock_key, user)


@router.post("/api/probes/{probe_id}/offline")
def set_offline(probe_id: int, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        row = conn.execute("SELECT * FROM probes WHERE id=?", (probe_id,)).fetchone()
        if not row:
            raise HTTPException(404, "探头不存在")
        conn.execute(
            "UPDATE probes SET online_status='离线' WHERE id=?", (probe_id,)
        )
        write_audit(conn, user, "probe_offline", row["probe_code"], {})
        conn.commit()
        return {"id": probe_id, "online_status": "离线"}

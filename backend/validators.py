"""温度范围、探头在线、封批拦截等校验（须被业务模块调用）。"""
from __future__ import annotations

from fastapi import HTTPException

# 校准偏移阈（℃）：|offset| 超阈即偏移偏航，探头对外禁止标在线。
OFFSET_LIMIT_C = 0.5
# 超阈探头对外单轨在线标识（正式字段零改，仅此派生值对外）。
PROBE_BARRED_STATUS = "超阈禁线"


def offset_over_threshold(offset_c: float) -> bool:
    return abs(float(offset_c)) > OFFSET_LIMIT_C


def latest_calibration(conn, probe_id: int):
    return conn.execute(
        "SELECT * FROM calibration_records WHERE probe_id=? ORDER BY id DESC LIMIT 1",
        (probe_id,),
    ).fetchone()


def latest_calibrations_map(conn) -> dict:
    """全量探头最新校准一次取回，供列表页同源派生。"""
    rows = conn.execute(
        """SELECT c.* FROM calibration_records c
           JOIN (SELECT probe_id, MAX(id) mid FROM calibration_records
                 GROUP BY probe_id) m ON m.mid=c.id"""
    ).fetchall()
    return {r["probe_id"]: r for r in rows}


def effective_online_status(online_status: str, latest_cal) -> str:
    """对外在线标识唯一轨道：正式字段超阈即禁线，其余原样透传。"""
    if online_status != "在线":
        return online_status
    if latest_cal is not None and offset_over_threshold(latest_cal["offset_c"]):
        return PROBE_BARRED_STATUS
    return "在线"


def validate_temp_point(value: float, target_min: float, target_max: float) -> None:
    if value < -80 or value > 40:
        raise HTTPException(422, "温度点超出传感器量程")
    if value < target_min - 15 or value > target_max + 15:
        raise HTTPException(422, "温度点偏离仓间目标带过远")


def validate_probe_online(conn, row) -> None:
    """单轨口径：对外标识非在线（含超阈禁线）一律拒写批次。"""
    status = effective_online_status(row["online_status"], latest_calibration(conn, row["id"]))
    if status != "在线":
        raise HTTPException(409, f"探头{status}，禁止写批次")


def validate_calibration_not_overdue(row) -> None:
    """到期仅警告级：入库允许但封批会拦截。"""
    from datetime import date

    due = row["calibration_due"]
    if due and due < date.today().isoformat():
        # 允许继续，由 seal 阶段硬拦
        return


def require_calibration_fresh(row) -> None:
    from datetime import date

    if row["calibration_due"] < date.today().isoformat():
        raise HTTPException(409, "探头校准已到期，禁止封批")


def validate_batch_sealable(batch, open_excursions: int) -> None:
    if batch["status"] not in ("已入库", "超温处理中"):
        raise HTTPException(409, "批次状态不允许封批")
    if open_excursions > 0:
        raise HTTPException(409, "存在开放超温游标，须先闭环")
    if not batch["temp_points"]:
        raise HTTPException(409, "无温度点序列，禁止封批")


def summarize_points(points: list) -> tuple[float, float]:
    vals = [float(p.get("c", p.get("temp", 0))) for p in points if p]
    if not vals:
        return 0.0, 0.0
    return min(vals), max(vals)


def detect_excursion(points: list, threshold: float) -> float | None:
    peak = None
    for p in points:
        v = float(p.get("c", p.get("temp", 0)))
        if v > threshold:
            peak = v if peak is None else max(peak, v)
    return peak
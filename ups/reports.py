"""Daily UPS journal: JSON on disk, one-file HTML for the phone."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

TEMPLATE_NAME = "template.html"
_PACKAGE_TEMPLATE = Path(__file__).with_name("report_template.html")
_PLACEHOLDER = "/*__UPS_DATA__*/null"

IMPORTANT = {
    "on_battery",
    "on_battery_clear",
    "battery_low",
    "battery_low_clear",
    "fault",
    "fault_clear",
    "link_lost",
    "link",
    "battery_loose",
    "battery_loose_clear",
    "battery_swap",
    "voltage_low",
    "voltage_low_clear",
    "voltage_high",
    "voltage_high_clear",
    "voltage_crit_low",
    "voltage_crit_low_clear",
    "voltage_crit_high",
    "voltage_crit_high_clear",
    "battery_boost",
    "battery_boost_clear",
    "pc_shutdown",
    "pc_off",
    "pc_reboot",
    "pc_sleep",
    "pc_hibernate",
    "app_restart",
    "load_high",
}


def default_journal_dir() -> Path:
    return Path.home() / "UPS"


def resolve_journal_dir(value) -> Path:
    text = str(value or "").strip()
    path = Path(text) if text else default_journal_dir()
    if not path.is_absolute():
        path = Path.home() / path
    return path


def today_stamp(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


def parse_day(value: str | None) -> str | None:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def day_bounds(stamp: str) -> tuple[float, float]:
    start = datetime.strptime(stamp, "%Y-%m-%d")
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def journal_paths(root: Path) -> dict[str, Path]:
    base = Path(root)
    days = base / "days"
    return {
        "root": base,
        "days": days,
        "template": base / TEMPLATE_NAME,
        "summary_json": base / "summary.json",
        "summary_html": base / "summary.html",
    }


def ensure_journal(root: Path) -> dict[str, Path]:
    paths = journal_paths(root)
    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["days"].mkdir(parents=True, exist_ok=True)
    source = load_template()
    target = paths["template"]
    if not target.is_file() or target.read_text(encoding="utf-8") != source:
        target.write_text(source, encoding="utf-8")
    return paths


def load_template() -> str:
    return _PACKAGE_TEMPLATE.read_text(encoding="utf-8")


def day_json_path(root: Path, stamp: str) -> Path:
    return journal_paths(root)["days"] / f"{stamp}.json"


def build_day(conn, stamp: str) -> dict:
    start, end = day_bounds(stamp)
    rows = conn.execute(
        """
        SELECT ts, input_v, output_v, load_pct, battery_v, battery_pct,
               frequency, temperature, on_battery
        FROM readings
        WHERE ts >= ? AND ts < ? AND source = 'live'
        ORDER BY ts
        """,
        (start, end),
    ).fetchall()
    events = [
        {"t": row[0], "level": row[1], "code": row[2], "message": row[3]}
        for row in conn.execute(
            """
            SELECT ts, level, code, message FROM events
            WHERE ts >= ? AND ts < ?
            ORDER BY ts
            """,
            (start, end),
        )
    ]
    return assemble_day(stamp, rows, events)


def assemble_day(stamp: str, rows, events: list[dict]) -> dict:
    buckets: dict[int, dict] = {}
    for row in rows:
        ts, incoming, outgoing, load, batt, soc, hz, temp, on_batt = row
        local = datetime.fromtimestamp(float(ts))
        minute = local.hour * 60 + local.minute
        slot = buckets.get(minute)
        if slot is None:
            slot = {
                "n": 0,
                "in": 0.0,
                "inN": 0,
                "inLo": None,
                "inHi": None,
                "out": 0.0,
                "outN": 0,
                "load": 0.0,
                "loadN": 0,
                "loadHi": None,
                "batt": 0.0,
                "battN": 0,
                "soc": 0.0,
                "socN": 0,
                "hz": 0.0,
                "hzN": 0,
                "temp": 0.0,
                "tempN": 0,
                "on": 0,
            }
            buckets[minute] = slot
        slot["n"] += 1
        if incoming is not None and incoming >= 80:
            slot["in"] += incoming
            slot["inN"] += 1
            slot["inLo"] = incoming if slot["inLo"] is None else min(slot["inLo"], incoming)
            slot["inHi"] = incoming if slot["inHi"] is None else max(slot["inHi"], incoming)
        if outgoing is not None:
            slot["out"] += outgoing
            slot["outN"] += 1
        if load is not None:
            slot["load"] += load
            slot["loadN"] += 1
            slot["loadHi"] = load if slot["loadHi"] is None else max(slot["loadHi"], load)
        if batt is not None:
            slot["batt"] += batt
            slot["battN"] += 1
        if soc is not None:
            slot["soc"] += soc
            slot["socN"] += 1
        if hz is not None:
            slot["hz"] += hz
            slot["hzN"] += 1
        if temp is not None:
            slot["temp"] += temp
            slot["tempN"] += 1
        if on_batt:
            slot["on"] += 1
    points = []
    for minute in sorted(buckets):
        slot = buckets[minute]
        count = max(1, slot["n"])
        points.append(
            {
                "m": minute,
                "in": _avg(slot["in"], slot["inN"], slot["inN"] > 0),
                "inLo": _round(slot["inLo"]),
                "inHi": _round(slot["inHi"]),
                "out": _avg(slot["out"], slot["outN"], slot["outN"] > 0),
                "load": _avg(slot["load"], slot["loadN"], slot["loadN"] > 0),
                "loadHi": _round(slot["loadHi"]),
                "batt": _avg(slot["batt"], slot["battN"], slot["battN"] > 0),
                "soc": _avg(slot["soc"], slot["socN"], slot["socN"] > 0),
                "hz": _avg(slot["hz"], slot["hzN"], slot["hzN"] > 0),
                "temp": _avg(slot["temp"], slot["tempN"], slot["tempN"] > 0),
                "on": round(slot["on"] / count, 2),
            }
        )
    return {
        "kind": "day",
        "date": stamp,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "points": points,
        "events": [_public_event(item) for item in events],
        "stats": day_stats(points, events),
    }


def day_stats(points: list[dict], events: list[dict]) -> dict:
    ins = [item["in"] for item in points if item.get("in") is not None]
    outs = [item["out"] for item in points if item.get("out") is not None]
    loads = [item["load"] for item in points if item.get("load") is not None]
    batts = [item["batt"] for item in points if item.get("batt") is not None]
    socs = [item["soc"] for item in points if item.get("soc") is not None]
    temps = [item["temp"] for item in points if item.get("temp") is not None]
    outages = sum(1 for item in events if item.get("code") == "on_battery")
    on_minutes = sum(1 for item in points if (item.get("on") or 0) >= 0.5)
    return {
        "samples": len(points),
        "inAvg": _mean(ins),
        "inMin": _round(min(ins) if ins else None),
        "inMax": _round(max(ins) if ins else None),
        "outAvg": _mean(outs),
        "loadAvg": _mean(loads),
        "loadMax": _round(max((item.get("loadHi") or item.get("load") or 0) for item in points) if points else None),
        "battAvg": _mean(batts),
        "socAvg": _mean(socs),
        "tempAvg": _mean(temps),
        "outages": outages,
        "onBatteryMinutes": on_minutes,
        "events": len(events),
    }


def compact_day(day: dict) -> dict:
    stats = dict(day.get("stats") or {})
    events = [
        _public_event(item)
        for item in day.get("events") or []
        if item.get("code") in IMPORTANT
    ]
    return {
        "date": day.get("date"),
        "stats": stats,
        "events": events[-24:],
    }


def build_summary(root: Path, extra: dict | None = None) -> dict:
    days: dict[str, dict] = {}
    summary_path = journal_paths(root)["summary_json"]
    if summary_path.is_file():
        try:
            stored = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        for item in stored.get("days") or []:
            if isinstance(item, dict) and item.get("date"):
                days[str(item["date"])] = item
    folder = journal_paths(root)["days"]
    if folder.is_dir():
        for path in folder.glob("*.json"):
            try:
                day = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if day.get("date"):
                days[str(day["date"])] = compact_day(day)
    if extra and extra.get("date"):
        days[str(extra["date"])] = compact_day(extra)
    ordered = [days[key] for key in sorted(days)]
    return {
        "kind": "summary",
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "days": ordered,
        "stats": summary_stats(ordered),
    }


def summary_stats(days: list[dict]) -> dict:
    ins = [item["stats"]["inAvg"] for item in days if (item.get("stats") or {}).get("inAvg") is not None]
    loads = [item["stats"]["loadAvg"] for item in days if (item.get("stats") or {}).get("loadAvg") is not None]
    outages = sum(int((item.get("stats") or {}).get("outages") or 0) for item in days)
    on_min = sum(int((item.get("stats") or {}).get("onBatteryMinutes") or 0) for item in days)
    return {
        "days": len(days),
        "inAvg": _mean(ins),
        "loadAvg": _mean(loads),
        "outages": outages,
        "onBatteryMinutes": on_min,
    }


def list_days(root: Path) -> list[str]:
    folder = journal_paths(root)["days"]
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("*.json") if parse_day(path.stem))


def load_day(root: Path, stamp: str) -> dict | None:
    path = day_json_path(root, stamp)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_day(root: Path, day: dict) -> Path:
    paths = ensure_journal(root)
    stamp = str(day.get("date") or today_stamp())
    path = paths["days"] / f"{stamp}.json"
    path.write_text(json.dumps(day, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path


def write_summary(root: Path, summary: dict) -> Path:
    paths = ensure_journal(root)
    paths["summary_json"].write_text(
        json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    paths["summary_html"].write_text(render_html(summary), encoding="utf-8")
    return paths["summary_html"]


def render_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    payload = payload.replace("<", "\\u003c")
    return load_template().replace(_PLACEHOLDER, payload)


def open_folder(root: Path) -> None:
    path = ensure_journal(root)["root"]
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
        return
    opener = shutil.which("xdg-open") or shutil.which("open")
    if opener:
        os.spawnl(os.P_NOWAIT, opener, opener, str(path))


def _public_event(item: dict) -> dict:
    return {
        "t": item.get("t"),
        "level": item.get("level") or "",
        "code": item.get("code") or "",
        "message": str(item.get("message") or "")[:180],
    }


def _avg(total: float, count: int, used: bool) -> float | None:
    if not used or count <= 0:
        return None
    return _round(total / count)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return _round(sum(values) / len(values))


def _round(value) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)

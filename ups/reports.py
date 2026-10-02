"""Daily UPS journal: JSON on disk, one-file HTML for the phone."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from ups.protocol import load_watts

TEMPLATE_NAME = "template.html"
_PACKAGE_TEMPLATE = Path(__file__).with_name("report_template.html")
_PLACEHOLDER = "/*__UPS_DATA__*/null"
ENERGY_MAX_GAP = 180.0
DEFAULT_KVA = 0.7

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
    "battery_buck",
    "battery_buck_clear",
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


def build_day(conn, stamp: str, kva: float | None = None) -> dict:
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
    return assemble_day(stamp, rows, events, kva=kva)


def assemble_day(stamp: str, rows, events: list[dict], kva: float | None = None) -> dict:
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
    rating = normalize_kva(kva)
    stats = day_stats(points, events)
    energy = energy_from_samples(rows, rating)
    for key, value in energy.items():
        if key not in {"samples", "hours"}:
            stats[key] = value
    return {
        "kind": "day",
        "date": stamp,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "kva": rating,
        "points": points,
        "events": [_public_event(item) for item in events],
        "stats": stats,
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
    energy = [float((item.get("stats") or {}).get("energyKwh") or 0) for item in days]
    energy_batt = [float((item.get("stats") or {}).get("energyKwhBattery") or 0) for item in days]
    peaks = [item["stats"]["peakW"] for item in days if (item.get("stats") or {}).get("peakW") is not None]
    return {
        "days": len(days),
        "inAvg": _mean(ins),
        "loadAvg": _mean(loads),
        "outages": outages,
        "onBatteryMinutes": on_min,
        "energyKwh": round(sum(energy), 3) if days else 0,
        "energyKwhBattery": round(sum(energy_batt), 3) if days else 0,
        "peakW": _round(max(peaks) if peaks else None),
    }


def normalize_kva(value) -> float:
    try:
        kva = float(value)
    except (TypeError, ValueError):
        kva = DEFAULT_KVA
    if kva <= 0:
        kva = DEFAULT_KVA
    return max(0.2, min(20.0, kva))


def energy_from_samples(rows, kva: float, start: float | None = None, end: float | None = None) -> dict:
    samples: list[tuple[float, float, bool]] = []
    rating = normalize_kva(kva)
    for row in rows or []:
        parsed = _row_sample(row)
        if parsed is None:
            continue
        ts, load, on_batt = parsed
        if start is not None and ts < start:
            continue
        if end is not None and ts >= end:
            continue
        watts = load_watts(load, rating)
        if watts is None:
            continue
        samples.append((ts, float(watts), on_batt))
    return _energy_of(samples, start, end)


def energy_from_points(points: list[dict], kva: float) -> dict:
    samples: list[tuple[float, float, bool]] = []
    rating = normalize_kva(kva)
    for index, point in enumerate(points or []):
        watts = load_watts(point.get("load"), rating)
        if watts is None:
            continue
        minute = int(point.get("m") or index)
        samples.append((float(minute * 60), float(watts), (point.get("on") or 0) >= 0.5))
    return _energy_of(samples, None, None, gap=90.0)


def ensure_energy(day: dict, kva: float | None = None, rows=None) -> dict:
    data = dict(day or {})
    rating = normalize_kva(kva if kva is not None else data.get("kva"))
    data["kva"] = rating
    stats = dict(data.get("stats") or {})
    if stats.get("energyKwh") is not None:
        data["stats"] = stats
        return data
    extra = None
    if rows:
        extra = energy_from_samples(rows, rating)
    elif data.get("points"):
        extra = energy_from_points(data.get("points") or [], rating)
    if extra and extra.get("samples"):
        for key, value in extra.items():
            if key not in {"samples", "hours"}:
                stats[key] = value
        data["stats"] = stats
    return data


def missing_energy(stats) -> bool:
    return (stats or {}).get("energyKwh") is None


def energy_rows(conn, stamp: str):
    start, end = day_bounds(stamp)
    return conn.execute(
        """
        SELECT ts, load_pct, on_battery FROM readings
        WHERE ts >= ? AND ts < ? AND source = 'live'
        ORDER BY ts
        """,
        (start, end),
    ).fetchall()


def upgrade_day_energy(conn, day: dict, kva: float | None = None) -> dict:
    data = dict(day or {})
    rating = normalize_kva(kva if kva is not None else data.get("kva"))
    if not missing_energy(data.get("stats")):
        data["kva"] = rating
        return data
    stamp = parse_day(data.get("date"))
    rows = energy_rows(conn, stamp) if conn is not None and stamp else None
    upgraded = ensure_energy(data, rating, rows)
    if missing_energy(upgraded.get("stats")) and stamp and conn is not None:
        built = build_day(conn, stamp, rating)
        if not missing_energy(built.get("stats")):
            return built
    return upgraded


def energy_bucket_step(start: float, end: float) -> float:
    span = max(0.0, float(end) - float(start))
    if span <= 8 * 3600:
        return 0.0
    if span <= 3 * 86400:
        return 15.0
    return 60.0


def energy_from_db(conn, kva: float, start: float, end: float) -> dict:
    lo = float(start)
    hi = float(end)
    step = energy_bucket_step(lo, hi)
    if step <= 0:
        rows = conn.execute(
            """
            SELECT ts, load_pct, on_battery FROM readings
            WHERE ts >= ? AND ts < ? AND source = 'live'
            ORDER BY ts
            """,
            (lo, hi),
        )
    else:
        rows = conn.execute(
            """
            SELECT CAST(ts / ? AS INTEGER) * ?,
                   AVG(load_pct),
                   CASE WHEN AVG(CAST(on_battery AS REAL)) >= 0.5 THEN 1 ELSE 0 END,
                   MAX(load_pct)
            FROM readings
            WHERE ts >= ? AND ts < ? AND source = 'live'
              AND load_pct IS NOT NULL
            GROUP BY CAST(ts / ? AS INTEGER)
            ORDER BY 1
            """,
            (step, step, lo, hi, step),
        )
    return energy_report(rows, kva, lo, hi)


def energy_report(rows, kva: float, start: float, end: float) -> dict:
    rating = normalize_kva(kva)
    lo = float(start)
    hi = float(end)
    span = max(1e-3, hi - lo)
    chart_limit = 480
    total = {"energy": 0.0, "counted": 0.0, "energy_batt": 0.0, "on_sec": 0.0}
    days_acc: dict[str, dict] = {}
    buckets: dict[int, tuple[float, float, bool]] = {}
    prev: tuple[float, float, bool] | None = None
    peak = None
    samples = 0
    watt_sum = 0.0
    for row in rows or []:
        parsed = _row_sample(row)
        if parsed is None:
            continue
        ts, load, on_batt = parsed
        if ts < lo or ts >= hi:
            continue
        watts = load_watts(load, rating)
        if watts is None:
            continue
        watts = float(watts)
        peak_load = load
        if not isinstance(row, dict):
            seq = tuple(row)
            if len(seq) == 4 and seq[3] is not None:
                peak_load = seq[3]
        peak_w = load_watts(peak_load, rating)
        if peak_w is not None:
            peak_w = float(peak_w)
            if peak is None or peak_w > peak:
                peak = peak_w
        samples += 1
        watt_sum += watts
        shown = peak_w if peak_w is not None else watts
        idx = min(chart_limit - 1, int((ts - lo) / span * (chart_limit - 1)))
        held = buckets.get(idx)
        if held is None or shown > held[1]:
            buckets[idx] = (ts, shown, on_batt)
        stamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        day = days_acc.get(stamp)
        if day is None:
            day = {
                "energy": 0.0, "counted": 0.0, "energy_batt": 0.0, "on_sec": 0.0,
                "peak": shown, "samples": 0, "watt_sum": 0.0, "prev": None,
            }
            days_acc[stamp] = day
        day["samples"] += 1
        day["watt_sum"] += watts
        if shown > day["peak"]:
            day["peak"] = shown
        if prev is not None:
            _add_slice(total, prev[0], prev[1], prev[2], ts, watts)
        if day["prev"] is not None:
            _add_slice(day, day["prev"][0], day["prev"][1], day["prev"][2], ts, watts)
        day["prev"] = (ts, watts, on_batt)
        prev = (ts, watts, on_batt)
    extra = watt_sum / samples if samples else None
    stats = _pack_energy(
        total["energy"], total["energy_batt"], total["counted"], total["on_sec"],
        peak, samples, lo, hi, extra,
    )
    days = []
    for stamp in sorted(days_acc):
        day = days_acc[stamp]
        day_start, day_end = day_bounds(stamp)
        day_extra = day["watt_sum"] / day["samples"] if day["samples"] else None
        days.append({
            "date": stamp,
            **_pack_energy(
                day["energy"], day["energy_batt"], day["counted"], day["on_sec"],
                day["peak"], day["samples"], max(day_start, lo), min(day_end, hi), day_extra,
            ),
        })
    points = [
        {"t": ts, "w": round(watts), "on": int(on_batt)}
        for ts, watts, on_batt in (buckets[idx] for idx in sorted(buckets))
    ]
    return {
        "ok": True,
        "from": lo,
        "to": hi,
        "kva": rating,
        **stats,
        "points": points,
        "days": days,
    }


def _energy_of(
    samples: list[tuple[float, float, bool]],
    start: float | None,
    end: float | None,
    gap: float = ENERGY_MAX_GAP,
) -> dict:
    energy = 0.0
    energy_batt = 0.0
    counted = 0.0
    on_sec = 0.0
    peak = None
    for ts, watts, on_batt in samples:
        if peak is None or watts > peak:
            peak = watts
    for index in range(1, len(samples)):
        t0, w0, on0 = samples[index - 1]
        t1, w1, _on1 = samples[index]
        dt = t1 - t0
        if dt <= 0:
            continue
        dt = min(dt, gap)
        hours = dt / 3600.0
        slice_kwh = ((w0 + w1) / 2.0) * hours / 1000.0
        energy += slice_kwh
        counted += dt
        if on0:
            energy_batt += slice_kwh
            on_sec += dt
    extra = None
    if counted <= 0 and samples:
        extra = sum(item[1] for item in samples) / len(samples)
    wall_start, wall_end = start, end
    if (wall_start is None or wall_end is None) and len(samples) >= 2:
        wall_start, wall_end = samples[0][0], samples[-1][0]
    return _pack_energy(energy, energy_batt, counted, on_sec, peak, len(samples), wall_start, wall_end, extra)


def _pack_energy(
    energy: float,
    energy_batt: float,
    counted: float,
    on_sec: float,
    peak: float | None,
    samples: int,
    start: float | None,
    end: float | None,
    extra_avg: float | None = None,
) -> dict:
    wall = 0.0
    if start is not None and end is not None and end > start:
        wall = end - start
    avg = None
    if counted > 0:
        avg = energy * 1000.0 / (counted / 3600.0)
    elif extra_avg is not None:
        avg = extra_avg
    return {
        "energyKwh": round(energy, 3),
        "energyKwhBattery": round(energy_batt, 3),
        "energyKwhMains": round(max(0.0, energy - energy_batt), 3),
        "avgW": round(avg) if avg is not None else None,
        "peakW": round(peak) if peak is not None else None,
        "hours": round(wall / 3600.0, 2) if wall else 0,
        "samples": samples,
        "onBatteryMinutes": int(round(on_sec / 60.0)),
    }


def _add_slice(acc: dict, t0: float, w0: float, on0: bool, t1: float, w1: float, gap: float = ENERGY_MAX_GAP) -> None:
    dt = t1 - t0
    if dt <= 0:
        return
    dt = min(dt, gap)
    slice_kwh = ((w0 + w1) / 2.0) * (dt / 3600.0) / 1000.0
    acc["energy"] += slice_kwh
    acc["counted"] += dt
    if on0:
        acc["energy_batt"] += slice_kwh
        acc["on_sec"] += dt


def _row_sample(row) -> tuple[float, object, bool] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        ts = row.get("t", row.get("ts"))
        if ts is None:
            return None
        load = row.get("load", row.get("load_pct"))
        on_batt = row.get("on", row.get("on_battery"))
        return float(ts), load, bool(on_batt)
    seq = tuple(row)
    if len(seq) >= 9:
        return float(seq[0]), seq[3], bool(seq[8])
    if len(seq) >= 3:
        return float(seq[0]), seq[1], bool(seq[2])
    if len(seq) >= 2:
        return float(seq[0]), seq[1], False
    return None


def _chart_points(samples: list[tuple[float, float, bool]], limit: int = 480) -> list[dict]:
    if len(samples) <= limit:
        return [{"t": ts, "w": round(watts), "on": int(on_batt)} for ts, watts, on_batt in samples]
    t0 = samples[0][0]
    span = max(1e-3, samples[-1][0] - t0)
    buckets: dict[int, tuple[float, float, bool]] = {}
    for ts, watts, on_batt in samples:
        idx = min(limit - 1, int((ts - t0) / span * (limit - 1)))
        prev = buckets.get(idx)
        if prev is None or watts > prev[1]:
            buckets[idx] = (ts, watts, on_batt)
    return [
        {"t": ts, "w": round(watts), "on": int(on_batt)}
        for ts, watts, on_batt in (buckets[idx] for idx in sorted(buckets))
    ]


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

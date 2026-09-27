"""Megatec Q1 parsing and the battery maths UPSmart was using."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Status bits, left to right, as defined by the Megatec Q1 protocol.
_BIT_NAMES = (
    "utility_fail",
    "battery_low",
    "avr",
    "fault",
    "offline_type",
    "test",
    "shutdown",
    "beeper",
)

_Q1 = re.compile(
    r"(\d{2,3}\.\d+)\s+"
    r"(\d{2,3}\.\d+)\s+"
    r"(\d{2,3}\.\d+)\s+"
    r"(\d{1,3}(?:\.\d+)?)\s+"
    r"(\d{2,3}\.\d+)\s+"
    r"(\d{1,3}\.\d+)\s+"
    r"(\S+)\s+"
    r"([01]{8})"
)

# Rating voltage, rating current, battery voltage, nominal frequency.
_RATING = re.compile(
    r"(\d{2,3}\.\d+)\s+"
    r"(\d+(?:\.\d+)?)\s+"
    r"(\d+\.\d+)\s+"
    r"(\d{2,3}\.\d+)"
)

# Load percent -> minutes, taken from the discharge table configured
# for this 0.7 kVA UPS and a 12 Ah string. Higher load, shorter time.
_RUNTIME_TABLE = (
    (2, 1200),
    (3, 600),
    (5, 360),
    (6, 300),
    (7, 240),
    (9, 180),
    (12, 120),
    (15, 90),
    (23, 60),
    (29, 45),
    (40, 30),
    (52, 20),
    (61, 15),
    (78, 10),
    (106, 5),
)


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Q1:
    input_v: float
    fault_v: float
    output_v: float
    load_pct: float
    frequency: float
    battery_v: float
    temperature: float | None
    utility_fail: bool
    battery_low: bool
    avr: bool
    fault: bool
    offline_type: bool
    test: bool
    shutdown: bool
    beeper: bool
    raw: str

    @property
    def on_battery(self) -> bool:
        return self.utility_fail

    def as_dict(self) -> dict:
        return {
            "input_v": self.input_v,
            "fault_v": self.fault_v,
            "output_v": self.output_v,
            "load_pct": self.load_pct,
            "frequency": self.frequency,
            "battery_v": self.battery_v,
            "temperature": self.temperature,
            "utility_fail": self.utility_fail,
            "battery_low": self.battery_low,
            "avr": self.avr,
            "fault": self.fault,
            "offline_type": self.offline_type,
            "test": self.test,
            "shutdown": self.shutdown,
            "beeper": self.beeper,
            "on_battery": self.on_battery,
            "raw": self.raw,
        }


def parse_q1(raw: str) -> Q1:
    text = raw.replace("\x00", " ").replace("\r", " ").replace("\n", " ")
    match = _Q1.search(text)
    if not match:
        raise ProtocolError(f"не похоже на ответ Q1: {raw!r}")
    input_v, fault_v, output_v, load, freq, batt, temp, bits = match.groups()
    flags = {name: bits[i] == "1" for i, name in enumerate(_BIT_NAMES)}
    return Q1(
        input_v=float(input_v),
        fault_v=float(fault_v),
        output_v=float(output_v),
        load_pct=float(load),
        frequency=float(freq),
        battery_v=float(batt),
        temperature=_temperature(temp),
        utility_fail=flags["utility_fail"],
        battery_low=flags["battery_low"],
        avr=flags["avr"],
        fault=flags["fault"],
        offline_type=flags["offline_type"],
        test=flags["test"],
        shutdown=flags["shutdown"],
        beeper=flags["beeper"],
        raw=match.group(0),
    )


def parse_ratings(raw: str) -> dict | None:
    match = _RATING.search(raw.replace("\x00", " "))
    if not match:
        return None
    voltage, current, battery, freq = (float(part) for part in match.groups())
    if voltage < 50 or battery < 5:
        return None
    return {
        "voltage": voltage,
        "current": current,
        "battery_v": battery,
        "frequency": freq,
        "va": round(voltage * current),
        "raw": match.group(0),
    }


def parse_info(raw: str) -> str | None:
    text = raw.replace("\x00", " ").replace("\r", " ").replace("#", " ")
    text = " ".join(text.split())
    if not text or text.upper().startswith("UPS NO ACK"):
        return None
    return text


def plausible(status: Q1) -> bool:
    """Reject the all-zero frames this UPS sometimes inserts between real ones."""
    if status.output_v < 20 or status.battery_v < 5:
        return False
    if not status.on_battery and status.input_v < 50:
        return False
    return True


def _temperature(token: str) -> float | None:
    if any(char.isalpha() for char in token) or set(token) <= set("-."):
        return None
    try:
        return float(token)
    except ValueError:
        return None


def battery_percent(
    pack_v: float | None,
    series: int,
    on_battery: bool,
    full_v: float,
    eod_v: float,
) -> float | None:
    """Charge percent from pack voltage.

    On mains, a battery sitting near its float voltage is full. 26.6 V on a
    2×12 V string with a 13.5 V full point is what UPSmart showed as 100%.
    """
    if pack_v is None or series <= 0 or full_v <= eod_v:
        return None
    each = pack_v / series
    if each < 4:
        return _clamp((each - 1.75) / (2.27 - 1.75) * 100)
    # On mains the charger holds a 12 V battery near 13 V. UPSmart shows
    # that as 100%, including 26.4 V on this 24 V pack.
    if not on_battery and each >= full_v - 0.8:
        return 100.0
    if not on_battery:
        return _clamp((each - eod_v) / (full_v - eod_v) * 100)
    curve = (
        (eod_v, 0),
        (11.2, 8),
        (11.6, 25),
        (11.9, 45),
        (12.15, 60),
        (12.4, 78),
        (12.6, 90),
        (12.85, 100),
    )
    return _clamp(_interp(each, curve))


def runtime_minutes(
    load_pct: float | None,
    amp_hours: float,
    kva: float,
    health_pct: float,
    *,
    base_ah: float = 12,
    base_kva: float = 0.7,
) -> int | None:
    """Estimated autonomy in minutes from the configured discharge table."""
    if load_pct is None or load_pct < 1 or amp_hours <= 0 or kva <= 0:
        return None
    base = _interp(load_pct, _RUNTIME_TABLE)
    scale = (amp_hours / base_ah) * (base_kva / kva) * (health_pct / 100)
    return max(1, int(round(base * scale)))


def mode_label(status: Q1) -> str:
    if status.fault:
        return "Авария ИБП"
    if status.test:
        return "Тест батареи"
    if status.on_battery:
        return "Работа от батареи"
    if status.avr:
        return "Стабилизация AVR"
    return "Нормальный режим"


def ups_kind(status: Q1) -> str:
    if status.offline_type:
        return "Линейно-интерактивный"
    return "Онлайн"


VOLTAGE_LOW = 200.0
VOLTAGE_HIGH = 240.0
VOLTAGE_CRIT_LOW = 180.0
VOLTAGE_CRIT_HIGH = 250.0
_VOLTAGE_HYST = 5.0
_MAINS_PRESENT = 80.0


def voltage_limits(settings: dict | None = None) -> dict:
    src = settings or {}
    return {
        "low": _limit_num(src.get("volt_low"), VOLTAGE_LOW),
        "high": _limit_num(src.get("volt_high"), VOLTAGE_HIGH),
        "crit_low": _limit_num(src.get("volt_crit_low"), VOLTAGE_CRIT_LOW),
        "crit_high": _limit_num(src.get("volt_crit_high"), VOLTAGE_CRIT_HIGH),
    }


def mains_present(volts, on_battery: bool) -> bool:
    try:
        return (not on_battery) and volts is not None and float(volts) >= _MAINS_PRESENT
    except (TypeError, ValueError):
        return False


def voltage_state(volts, on_battery: bool, limits: dict | None = None) -> str:
    if not mains_present(volts, on_battery):
        return "idle"
    bands = voltage_limits(limits)
    value = float(volts)
    if value < bands["crit_low"]:
        return "crit_low"
    if value < bands["low"]:
        return "low"
    if value > bands["crit_high"]:
        return "crit_high"
    if value > bands["high"]:
        return "high"
    return "ok"


def voltage_note(state: str, limits: dict | None = None) -> str | None:
    bands = voltage_limits(limits)
    return {
        "low": f"Вход ниже {bands['low']:.0f} В",
        "high": f"Вход выше {bands['high']:.0f} В",
        "crit_low": f"Вход ниже {bands['crit_low']:.0f} В",
        "crit_high": f"Вход выше {bands['crit_high']:.0f} В",
    }.get(state)


def mains_voltage_active(code: str, volts, on_battery: bool, previous: bool, limits: dict | None = None) -> bool:
    if not mains_present(volts, on_battery):
        return False
    bands = voltage_limits(limits)
    hyst = _VOLTAGE_HYST
    table = {
        "voltage_low": (bands["low"], bands["low"] + hyst, "below"),
        "voltage_crit_low": (bands["crit_low"], bands["crit_low"] + hyst, "below"),
        "voltage_high": (bands["high"], bands["high"] - hyst, "above"),
        "voltage_crit_high": (bands["crit_high"], bands["crit_high"] - hyst, "above"),
    }
    trigger, clear, side = table[code]
    value = float(volts)
    if side == "below":
        return value < trigger if not previous else value < clear
    return value > trigger if not previous else value > clear


def _limit_num(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def load_watts(load_pct: float | None, kva: float | None) -> int | None:
    if load_pct is None or kva is None:
        return None
    try:
        return max(0, int(round(float(kva) * 10 * float(load_pct))))
    except (TypeError, ValueError):
        return None


def battery_voltage_chaotic(values: list[float], min_span: float = 1.8, min_reversals: int = 4) -> bool:
    """True when pack voltage jumps around — typical of a disconnected battery."""
    if len(values) < 8:
        return False
    span = max(values) - min(values)
    if span < min_span:
        return False
    reversals = 0
    previous = 0.0
    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        if abs(delta) < 0.15:
            continue
        if previous and delta * previous < 0:
            reversals += 1
        previous = delta
    return reversals >= min_reversals


def battery_voltage_stepped(
    values: list[float],
    min_step: float = 1.0,
    plateau: int = 5,
    max_wiggle: float = 0.45,
) -> tuple[float, float] | None:
    """A sharp move to a new stable voltage — the pack was swapped, not flapping."""
    if len(values) < plateau * 2:
        return None
    if battery_voltage_chaotic(values):
        return None
    jump_at = None
    for index in range(len(values) - 1, 0, -1):
        if abs(values[index] - values[index - 1]) >= min_step:
            jump_at = index
            break
    if jump_at is None:
        return None
    before = values[max(0, jump_at - plateau):jump_at]
    after = values[jump_at:jump_at + plateau]
    if len(before) < plateau or len(after) < plateau:
        return None
    if max(before) - min(before) > max_wiggle:
        return None
    if max(after) - min(after) > max_wiggle:
        return None
    old = sum(before) / len(before)
    new = sum(after) / len(after)
    if abs(new - old) < min_step:
        return None
    return (old, new)


def _interp(value: float, points: tuple[tuple[float, float], ...]) -> float:
    ordered = sorted(points, key=lambda item: item[0])
    if value <= ordered[0][0]:
        return ordered[0][1]
    if value >= ordered[-1][0]:
        return ordered[-1][1]
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 <= value <= x1:
            span = x1 - x0
            if span == 0:
                return y0
            return y0 + (value - x0) / span * (y1 - y0)
    return ordered[-1][1]


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))

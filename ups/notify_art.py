"""PNG cards for Telegram alerts. Drawn pictures, not emoji."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 768
_CACHE: dict[tuple[str, str], bytes] = {}

INK = (16, 20, 15, 255)
PANEL = (23, 30, 24, 255)
TEXT = (232, 242, 234, 255)
MUTED = (147, 167, 154, 255)
WHITE = (255, 255, 255, 255)

OK = (61, 190, 122, 255)
BAD = (225, 93, 76, 255)
WARN = (230, 177, 92, 255)
SKY = (143, 208, 255, 255)
COPPER = (224, 176, 106, 255)

_TITLES = {
    "on_battery": "Сеть пропала",
    "on_battery_clear": "Сеть вернулась",
    "battery_low": "Батарея садится",
    "battery_low_clear": "Батарея в норме",
    "fault": "Неисправность",
    "fault_clear": "Авария снята",
    "link_lost": "Нет связи с ИБП",
    "battery_loose": "Батареи отключены",
    "battery_loose_clear": "Пакет в норме",
    "battery_swap": "Пакет сменился",
    "pc_shutdown": "Выключение ПК",
    "pc_off": "Выключить ПК",
    "pc_reboot": "Перезагрузка",
    "pc_sleep": "Сон",
    "pc_hibernate": "Гибернация",
    "startup": "UPS запущен",
    "shutdown": "UPS закрыт",
    "report": "Отчёт",
    "status": "Состояние",
    "events": "Журнал",
    "screenshot": "Главное окно",
    "help": "Справка",
    "cancel": "Отменено",
    "app_restart": "Перезапуск UPS",
    "day_report": "Отчёт дня",
    "summary": "Сводка дней",
    "voltage_low": "Сеть ниже 200 В",
    "voltage_low_clear": "Сеть выше 200 В",
    "voltage_high": "Сеть выше 240 В",
    "voltage_high_clear": "Сеть ниже 240 В",
    "voltage_crit_low": "Сеть ниже 180 В",
    "voltage_crit_low_clear": "Спад снят",
    "voltage_crit_high": "Сеть выше 250 В",
    "voltage_crit_high_clear": "Скачок снят",
}

_STYLES = {
    "on_battery": BAD,
    "on_battery_clear": OK,
    "battery_low": WARN,
    "battery_low_clear": OK,
    "fault": BAD,
    "fault_clear": OK,
    "link_lost": SKY,
    "battery_loose": WARN,
    "battery_loose_clear": OK,
    "battery_swap": COPPER,
    "pc_shutdown": WARN,
    "pc_off": COPPER,
    "pc_reboot": COPPER,
    "pc_sleep": SKY,
    "pc_hibernate": SKY,
    "startup": OK,
    "shutdown": MUTED,
    "report": COPPER,
    "status": COPPER,
    "events": COPPER,
    "screenshot": SKY,
    "help": COPPER,
    "cancel": MUTED,
    "app_restart": COPPER,
    "day_report": COPPER,
    "summary": COPPER,
    "voltage_low": WARN,
    "voltage_low_clear": OK,
    "voltage_high": WARN,
    "voltage_high_clear": OK,
    "voltage_crit_low": BAD,
    "voltage_crit_low_clear": OK,
    "voltage_crit_high": BAD,
    "voltage_crit_high_clear": OK,
}


def notify_image(code: str, title: str | None = None) -> bytes:
    key_title = (title or _TITLES.get(code) or "UPS").strip()
    cache_key = (code, key_title)
    hit = _CACHE.get(cache_key)
    if hit:
        return hit
    png = _render(code, key_title)
    _CACHE[cache_key] = png
    return png


def card_title(code: str, text: str | None = None) -> str:
    if code in _TITLES:
        return _TITLES[code]
    first = str(text or "").strip().splitlines()[0].strip()
    return first or "UPS"


def _render(code: str, title: str) -> bytes:
    image = Image.new("RGBA", (SIZE, SIZE), INK)
    draw = ImageDraw.Draw(image)
    accent = _STYLES.get(code, COPPER)
    _panel(draw)
    cx = cy = 318
    radius = 156
    draw.ellipse((cx - radius - 18, cy - radius - 18, cx + radius + 18, cy + radius + 18), fill=_fade(accent, 38))
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=_fade(accent, 210))
    _pictogram(draw, code, cx, cy, accent)
    font_title, lines, step = _fit_title(draw, title)
    y = 528
    for line in lines:
        draw.text((SIZE / 2, y), line, font=font_title, fill=TEXT, anchor="mm")
        y += step
    draw.text((SIZE / 2, 720), "UPS", font=_font(22), fill=MUTED, anchor="mm")
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _panel(draw: ImageDraw.ImageDraw) -> None:
    draw.rounded_rectangle((28, 28, SIZE - 28, SIZE - 28), 36, fill=PANEL)
    for i in range(8):
        x = 80 + i * 80
        draw.line((x, 70, x, 520), fill=(255, 255, 255, 10), width=1)
        if x < 520:
            draw.line((70, x, 698, x), fill=(255, 255, 255, 10), width=1)


def _pictogram(draw: ImageDraw.ImageDraw, code: str, cx: int, cy: int, accent) -> None:
    w = WHITE
    if code == "on_battery":
        _plug(draw, cx - 16, cy, w)
        _slash(draw, cx, cy, w)
    elif code == "on_battery_clear":
        _plug(draw, cx, cy, w)
    elif code == "battery_low":
        _battery(draw, cx, cy, w, fill=1)
    elif code in {"battery_low_clear", "battery_loose_clear"}:
        _battery(draw, cx, cy, w, fill=4)
    elif code == "fault":
        _triangle(draw, cx, cy, w)
    elif code == "fault_clear":
        _check(draw, cx, cy, w)
    elif code == "link_lost":
        _antenna(draw, cx, cy - 8, w)
        _slash(draw, cx, cy, w)
    elif code == "battery_loose":
        _battery(draw, cx, cy, w, fill=0)
        draw.line((cx - 70, cy + 78, cx + 70, cy + 78), fill=w, width=10)
    elif code == "battery_swap":
        _swap_pack(draw, cx, cy, w)
    elif code == "pc_off":
        _power(draw, cx, cy, w)
    elif code == "pc_reboot":
        _arrows(draw, cx, cy, w)
    elif code == "pc_sleep":
        _moon(draw, cx, cy, w)
    elif code == "pc_hibernate":
        _zzz(draw, cx, cy, w)
    elif code == "pc_shutdown":
        _clock(draw, cx, cy, w)
    elif code == "startup":
        _pulse(draw, cx, cy, w)
    elif code == "shutdown":
        _cross(draw, cx, cy, w)
    elif code == "report":
        _bars(draw, cx, cy, w)
    elif code == "status":
        _gauge(draw, cx, cy, w)
    elif code == "events":
        _list(draw, cx, cy, w)
    elif code == "screenshot":
        _monitor(draw, cx, cy, w)
    elif code == "app_restart":
        _arrows(draw, cx, cy, w)
    elif code in {"day_report", "summary"}:
        _bars(draw, cx, cy, w)
    elif code == "voltage_low":
        _mains_arrow(draw, cx, cy, w, up=False, critical=False)
    elif code == "voltage_high":
        _mains_arrow(draw, cx, cy, w, up=True, critical=False)
    elif code == "voltage_crit_low":
        _mains_arrow(draw, cx, cy, w, up=False, critical=True)
    elif code == "voltage_crit_high":
        _mains_arrow(draw, cx, cy, w, up=True, critical=True)
    elif code in {"voltage_low_clear", "voltage_high_clear", "voltage_crit_low_clear", "voltage_crit_high_clear"}:
        _check(draw, cx, cy, w)
    elif code == "help":
        _info(draw, cx, cy, w)
    elif code == "cancel":
        _cross(draw, cx, cy, w)
    else:
        _pulse(draw, cx, cy, w)


def _plug(draw, cx, cy, color) -> None:
    draw.rounded_rectangle((cx - 54, cy - 20, cx + 36, cy + 20), 8, outline=color, width=12)
    draw.rectangle((cx + 36, cy - 10, cx + 78, cy + 10), fill=color)
    draw.line((cx - 28, cy - 48, cx - 28, cy - 20), fill=color, width=12)
    draw.line((cx + 4, cy - 48, cx + 4, cy - 20), fill=color, width=12)


def _mains_arrow(draw, cx, cy, color, up: bool, critical: bool) -> None:
    _plug(draw, cx, cy - 28, color)
    if up:
        draw.polygon([(cx, cy + 8), (cx - 46, cy + 72), (cx + 46, cy + 72)], fill=color)
    else:
        draw.polygon([(cx, cy + 88), (cx - 46, cy + 24), (cx + 46, cy + 24)], fill=color)
    if critical:
        draw.line((cx - 88, cy + 96, cx + 88, cy - 100), fill=color, width=14)


def _battery(draw, cx, cy, color, fill: int) -> None:
    draw.rounded_rectangle((cx - 78, cy - 44, cx + 62, cy + 44), 12, outline=color, width=12)
    draw.rounded_rectangle((cx + 62, cy - 16, cx + 82, cy + 16), 4, fill=color)
    gap = 8
    inner = 28
    for i in range(fill):
        left = cx - 62 + i * (inner + gap)
        draw.rounded_rectangle((left, cy - 24, left + inner, cy + 24), 4, fill=color)


def _swap_pack(draw, cx, cy, color) -> None:
    draw.rounded_rectangle((cx - 92, cy - 58, cx - 8, cy + 8), 10, outline=color, width=10)
    draw.rectangle((cx - 62, cy - 72, cx - 38, cy - 58), fill=color)
    draw.rounded_rectangle((cx + 8, cy - 8, cx + 92, cy + 58), 10, outline=color, width=10)
    draw.rectangle((cx + 38, cy - 22, cx + 62, cy - 8), fill=color)
    draw.line((cx - 6, cy + 36, cx + 6, cy + 52), fill=color, width=8)
    draw.polygon([(cx + 18, cy + 40), (cx + 2, cy + 70), (cx - 10, cy + 48)], fill=color)


def _triangle(draw, cx, cy, color) -> None:
    draw.polygon([(cx, cy - 88), (cx + 96, cy + 78), (cx - 96, cy + 78)], outline=color)
    draw.line((cx, cy - 88, cx + 96, cy + 78), fill=color, width=12)
    draw.line((cx + 96, cy + 78, cx - 96, cy + 78), fill=color, width=12)
    draw.line((cx - 96, cy + 78, cx, cy - 88), fill=color, width=12)
    draw.rounded_rectangle((cx - 8, cy - 28, cx + 8, cy + 18), 4, fill=color)
    draw.ellipse((cx - 9, cy + 36, cx + 9, cy + 54), fill=color)


def _check(draw, cx, cy, color) -> None:
    draw.ellipse((cx - 92, cy - 92, cx + 92, cy + 92), outline=color, width=12)
    draw.line((cx - 48, cy + 4, cx - 12, cy + 44), fill=color, width=14)
    draw.line((cx - 12, cy + 44, cx + 56, cy - 36), fill=color, width=14)


def _antenna(draw, cx, cy, color) -> None:
    draw.line((cx, cy + 70, cx, cy - 10), fill=color, width=12)
    draw.ellipse((cx - 14, cy + 62, cx + 14, cy + 90), fill=color)
    draw.arc((cx - 56, cy - 50, cx + 56, cy + 62), 200, 340, fill=color, width=12)
    draw.arc((cx - 88, cy - 78, cx + 88, cy + 70), 205, 335, fill=color, width=12)


def _power(draw, cx, cy, color) -> None:
    draw.arc((cx - 78, cy - 70, cx + 78, cy + 86), 130, 50, fill=color, width=14)
    draw.line((cx, cy - 86, cx, cy + 4), fill=color, width=14)


def _arrows(draw, cx, cy, color) -> None:
    draw.arc((cx - 78, cy - 78, cx + 78, cy + 78), 40, 200, fill=color, width=12)
    draw.arc((cx - 78, cy - 78, cx + 78, cy + 78), 220, 20, fill=color, width=12)
    draw.polygon([(cx + 58, cy - 70), (cx + 92, cy - 38), (cx + 42, cy - 30)], fill=color)
    draw.polygon([(cx - 58, cy + 70), (cx - 92, cy + 38), (cx - 42, cy + 30)], fill=color)


def _moon(draw, cx, cy, color) -> None:
    draw.ellipse((cx - 64, cy - 70, cx + 56, cy + 70), fill=color)
    draw.ellipse((cx - 8, cy - 86, cx + 96, cy + 48), fill=_fade(SKY, 210))


def _zzz(draw, cx, cy, color) -> None:
    font = _font(72, bold=True)
    draw.text((cx - 36, cy - 8), "Z", font=font, fill=color, anchor="mm")
    draw.text((cx + 18, cy - 48), "z", font=_font(48, bold=True), fill=color, anchor="mm")
    draw.text((cx + 54, cy - 84), "z", font=_font(32, bold=True), fill=color, anchor="mm")


def _clock(draw, cx, cy, color) -> None:
    draw.ellipse((cx - 86, cy - 86, cx + 86, cy + 86), outline=color, width=12)
    draw.line((cx, cy, cx, cy - 44), fill=color, width=12)
    draw.line((cx, cy, cx + 36, cy + 20), fill=color, width=12)


def _pulse(draw, cx, cy, color) -> None:
    draw.ellipse((cx - 92, cy - 92, cx + 92, cy + 92), outline=color, width=10)
    draw.line((cx - 78, cy + 8, cx - 42, cy + 8), fill=color, width=12)
    draw.line((cx - 42, cy + 8, cx - 18, cy - 52), fill=color, width=12)
    draw.line((cx - 18, cy - 52, cx + 16, cy + 56), fill=color, width=12)
    draw.line((cx + 16, cy + 56, cx + 40, cy + 8), fill=color, width=12)
    draw.line((cx + 40, cy + 8, cx + 78, cy + 8), fill=color, width=12)


def _bars(draw, cx, cy, color) -> None:
    draw.rounded_rectangle((cx - 78, cy + 10, cx - 38, cy + 70), 6, fill=color)
    draw.rounded_rectangle((cx - 20, cy - 30, cx + 20, cy + 70), 6, fill=color)
    draw.rounded_rectangle((cx + 38, cy - 70, cx + 78, cy + 70), 6, fill=color)


def _gauge(draw, cx, cy, color) -> None:
    draw.arc((cx - 90, cy - 70, cx + 90, cy + 110), 200, 340, fill=color, width=14)
    draw.line((cx, cy + 20, cx + 54, cy - 36), fill=color, width=12)
    draw.ellipse((cx - 10, cy + 12, cx + 10, cy + 32), fill=color)


def _list(draw, cx, cy, color) -> None:
    for i, y in enumerate((-48, 0, 48)):
        draw.ellipse((cx - 78, cy + y - 10, cx - 58, cy + y + 10), fill=color)
        draw.rounded_rectangle((cx - 40, cy + y - 8, cx + 78, cy + y + 8), 4, fill=color)


def _monitor(draw, cx, cy, color) -> None:
    draw.rounded_rectangle((cx - 92, cy - 70, cx + 92, cy + 42), 12, outline=color, width=12)
    draw.rectangle((cx - 20, cy + 42, cx + 20, cy + 62), fill=color)
    draw.rounded_rectangle((cx - 50, cy + 62, cx + 50, cy + 76), 4, fill=color)


def _info(draw, cx, cy, color) -> None:
    draw.ellipse((cx - 88, cy - 88, cx + 88, cy + 88), outline=color, width=12)
    draw.ellipse((cx - 10, cy - 52, cx + 10, cy - 32), fill=color)
    draw.rounded_rectangle((cx - 8, cy - 12, cx + 8, cy + 48), 4, fill=color)


def _cross(draw, cx, cy, color) -> None:
    draw.line((cx - 56, cy - 56, cx + 56, cy + 56), fill=color, width=16)
    draw.line((cx + 56, cy - 56, cx - 56, cy + 56), fill=color, width=16)


def _slash(draw, cx, cy, color) -> None:
    draw.line((cx - 88, cy + 78, cx + 88, cy - 78), fill=color, width=16)


def _fade(color, alpha: int):
    return (color[0], color[1], color[2], alpha)


def _fit_title(draw: ImageDraw.ImageDraw, title: str):
    max_width = SIZE - 80
    for size in (42, 36, 30, 26, 22):
        font = _font(size, bold=True)
        lines = _wrap(draw, title, font, max_width)
        step = size + 10
        if len(lines) * step <= 176:
            return font, lines, step
    font = _font(22, bold=True)
    return font, _wrap(draw, title, font, max_width), 30


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = str(text or "UPS").split()
    if not words:
        return ["UPS"]
    lines: list[str] = []
    current = ""
    for word in words:
        pieces = _split_word(draw, word, font, max_width)
        for piece in pieces:
            trial = f"{current} {piece}".strip()
            if current and draw.textlength(trial, font=font) > max_width:
                lines.append(current)
                current = piece
            else:
                current = trial
    if current:
        lines.append(current)
    return lines


def _split_word(draw: ImageDraw.ImageDraw, word: str, font, max_width: int) -> list[str]:
    if draw.textlength(word, font=font) <= max_width:
        return [word]
    parts: list[str] = []
    chunk = ""
    for char in word:
        trial = chunk + char
        if chunk and draw.textlength(trial, font=font) > max_width:
            parts.append(chunk)
            chunk = char
        else:
            chunk = trial
    if chunk:
        parts.append(chunk)
    return parts or [word]


def _font(size: int, bold: bool = False):
    names = (
        ("segoeuib.ttf", "segoeui.ttf") if bold else ("segoeui.ttf", "segoeuib.ttf")
    )
    roots = [Path(r"C:\Windows\Fonts"), Path("/usr/share/fonts/truetype/dejavu")]
    for root in roots:
        for name in names:
            path = root / name
            if path.is_file():
                return ImageFont.truetype(str(path), size)
            alt = root / name.replace("segoeui", "arial").replace("segoeuib", "arialbd")
            if alt.is_file():
                return ImageFont.truetype(str(alt), size)
    return ImageFont.load_default()

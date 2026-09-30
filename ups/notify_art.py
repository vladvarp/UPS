"""PNG of the same HUD notice card that pops on the PC."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCALE = 3
WIDTH = 420 * SCALE
HEIGHT = 210 * SCALE
SIZE = WIDTH
_CACHE: dict[tuple[str, str, str], bytes] = {}

INK = (11, 18, 24, 255)
TEXT = (231, 246, 255, 255)
MUTED = (127, 154, 170, 255)
CYAN = (77, 226, 255, 255)
GOLD = (224, 176, 106, 255)
OK = (124, 240, 192, 255)
BAD = (255, 177, 168, 255)
WARN = GOLD
SKY = OK
COPPER = GOLD

_TITLES = {
    "on_battery": "Сеть пропала",
    "on_battery_clear": "Сеть вернулась",
    "battery_low": "Батарея садится",
    "battery_low_clear": "Батарея в норме",
    "fault": "Неисправность",
    "fault_clear": "Авария снята",
    "link_lost": "Нет связи с ИБП",
    "link": "ИБП на связи",
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
    "voltage_low": "Сеть понижена",
    "voltage_low_clear": "Сеть в норме",
    "voltage_high": "Сеть повышена",
    "voltage_high_clear": "Сеть в норме",
    "voltage_crit_low": "Сеть слишком низкая",
    "voltage_crit_low_clear": "Спад снят",
    "voltage_crit_high": "Сеть слишком высокая",
    "voltage_crit_high_clear": "Скачок снят",
    "battery_boost": "Стабилизатор",
    "battery_boost_clear": "Стабилизатор отпустил",
    "battery_buck": "Стабилизатор",
    "battery_buck_clear": "Стабилизатор отпустил",
}


def notify_image(code: str, title: str | None = None, text: str | None = None) -> bytes:
    key_title = (title or _TITLES.get(code) or "UPS").strip()
    body = _card_body(text, key_title)
    cache_key = (code, key_title, body)
    hit = _CACHE.get(cache_key)
    if hit:
        return hit
    png = _render(code, key_title, body)
    _CACHE[cache_key] = png
    return png


def card_title(code: str, text: str | None = None) -> str:
    if code in _TITLES:
        return _TITLES[code]
    first = str(text or "").strip().splitlines()[0].strip()
    return first or "UPS"


def _card_body(text: str | None, title: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    first = raw.split("\n\n")[0].strip()
    lines = [line.strip() for line in first.splitlines() if line.strip()]
    if lines and lines[0] == title:
        lines = lines[1:]
    return " ".join(lines) if lines else first


def _tone(code: str) -> str:
    if "crit" in code or code in {"fault", "pc_off", "battery_loose"}:
        return "bad"
    if code.endswith("_clear") or code in {"startup", "link"}:
        return "ok"
    return "warn"


def _kicker(code: str, tone: str) -> str:
    if code == "link":
        return "Связь"
    if code in {"startup", "shutdown", "app_restart"}:
        return "Событие"
    if code in {"report", "day_report", "summary", "status", "events", "screenshot", "help"}:
        return "Сводка"
    if tone == "bad":
        return "Авария"
    if tone == "ok":
        return "Снято"
    return "Предупреждение"


def _accent(tone: str):
    if tone == "bad":
        return BAD
    if tone == "ok":
        return OK
    return GOLD


def _border(tone: str):
    if tone == "bad":
        return (225, 93, 76, 255)
    if tone == "ok":
        return (61, 190, 122, 255)
    return GOLD


def _render(code: str, title: str, body: str) -> bytes:
    tone = _tone(code)
    accent = _accent(tone)
    image = Image.new("RGBA", (WIDTH, HEIGHT), INK)
    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rectangle((0, 0, WIDTH, int(HEIGHT * 0.4)), fill=_fade(CYAN, 20))
    image.alpha_composite(glow)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=_border(tone), width=SCALE)
    _corners(draw, accent)
    _scan(image, accent)
    pad = 18 * SCALE
    art = 96 * SCALE
    gap = 14 * SCALE
    art_y = (HEIGHT - art) // 2
    _pc_glyph(draw, code, pad, art_y, art, accent)
    copy_x = pad + art + gap
    copy_w = WIDTH - copy_x - pad
    _tracked(draw, (copy_x, pad), _kicker(code, tone).upper(), _font(11 * SCALE, bold=True), accent, int(11 * SCALE * 0.16))
    title_font = _font(26 * SCALE, bold=True)
    title_y = pad + 22 * SCALE
    for line in _wrap(draw, title, title_font, copy_w)[:2]:
        draw.text((copy_x, title_y), line, font=title_font, fill=TEXT)
        title_y += int(26 * SCALE * 1.15)
    body_font = _font(14 * SCALE)
    body_y = title_y + 8 * SCALE
    for line in _wrap(draw, body, body_font, copy_w)[:3]:
        draw.text((copy_x, body_y), line, font=body_font, fill=MUTED)
        body_y += int(14 * SCALE * 1.45)
    stamp = datetime.now().strftime("%H:%M:%S")
    draw.text((copy_x, HEIGHT - pad - 14 * SCALE), stamp, font=_font(12 * SCALE), fill=MUTED)
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _corners(draw: ImageDraw.ImageDraw, accent) -> None:
    t = 16 * SCALE
    m = 8 * SCALE
    w = 2 * SCALE
    draw.line((m, m + t, m, m, m + t, m), fill=accent, width=w)
    draw.line((WIDTH - m - t, HEIGHT - m, WIDTH - m, HEIGHT - m, WIDTH - m, HEIGHT - m - t), fill=accent, width=w)


def _scan(image: Image.Image, accent) -> None:
    band = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    layer = ImageDraw.Draw(band)
    mid = HEIGHT // 2
    layer.rectangle((0, mid - 8 * SCALE, WIDTH, mid + 8 * SCALE), fill=_fade(accent, 28))
    image.alpha_composite(band)


def _tracked(draw, xy, text, font, fill, tracking) -> None:
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font) + tracking


def _pc_glyph(draw, code: str, x: int, y: int, size: int, color) -> None:
    s = size / 120
    sw = max(2, round(5 * s))

    def p(px, py):
        return (x + px * s, y + py * s)

    def line(*pairs):
        pts = [p(a, b) for a, b in zip(pairs[::2], pairs[1::2])]
        draw.line(pts, fill=color, width=sw, joint="curve")

    def rect(px, py, w, h, rx=0, fill=False):
        box = [p(px, py), p(px + w, py + h)]
        if fill:
            draw.rounded_rectangle(box, radius=max(1, round(rx * s)), fill=color)
        else:
            draw.rounded_rectangle(box, radius=max(1, round(rx * s)), outline=color, width=sw)

    def circle(cx, cy, r, fill=False):
        box = [p(cx - r, cy - r), p(cx + r, cy + r)]
        if fill:
            draw.ellipse(box, fill=color)
        else:
            draw.ellipse(box, outline=color, width=sw)

    def arc(cx, cy, r, start, end):
        draw.arc([p(cx - r, cy - r), p(cx + r, cy + r)], start, end, fill=color, width=sw)

    if code in {"on_battery", "battery_low"}:
        rect(22, 38, 62, 44, 4)
        rect(84, 52, 10, 16, 0, fill=True)
        line(34, 60, 44, 60)
        line(50, 60, 60, 60)
        line(66, 60, 74, 60)
        line(28, 22, 18, 8)
        line(96, 22, 106, 8)
        return
    if code in {"battery_boost", "battery_buck"}:
        rect(28, 36, 26, 48, 4)
        rect(66, 36, 26, 48, 4)
        line(54, 50, 66, 50)
        line(54, 70, 66, 70)
        if code == "battery_buck":
            draw.polygon([p(60, 32), p(72, 14), p(48, 14)], fill=color)
        else:
            draw.polygon([p(60, 14), p(72, 32), p(48, 32)], fill=color)
        return
    if code.endswith("_clear"):
        circle(60, 60, 36)
        line(40, 62, 54, 76, 82, 44)
        return
    if code.startswith("voltage_"):
        rect(28, 28, 48, 28, 4)
        rect(76, 36, 12, 12, 0, fill=True)
        line(42, 20, 42, 28)
        line(58, 20, 58, 28)
        if "high" in code:
            draw.polygon([p(60, 68), p(38, 96), p(82, 96)], fill=color)
        else:
            draw.polygon([p(60, 104), p(38, 76), p(82, 76)], fill=color)
        if "crit" in code:
            line(24, 100, 96, 20)
        return
    if code == "battery_loose":
        rect(18, 36, 36, 28, 3)
        rect(66, 56, 36, 28, 3)
        line(54, 50, 66, 70)
        return
    if code == "battery_swap":
        rect(16, 28, 36, 26, 3)
        rect(68, 66, 36, 26, 3)
        arc(48, 70, 24, 200, 340)
        draw.polygon([p(66, 62), p(72, 72), p(60, 74)], fill=color)
        return
    if code in {"pc_shutdown", "pc_off", "pc_reboot"}:
        circle(60, 60, 36)
        line(60, 32, 60, 60, 78, 70)
        return
    if code == "fault":
        draw.polygon([p(60, 18), p(104, 98), p(16, 98)], outline=color, width=sw)
        line(60, 46, 60, 70)
        circle(60, 84, 3, fill=True)
        return
    if code == "link_lost":
        line(60, 86, 60, 40)
        circle(60, 94, 6, fill=True)
        arc(60, 52, 22, 200, 340)
        arc(60, 46, 34, 205, 335)
        line(28, 100, 92, 20)
        return
    if code == "link":
        line(60, 86, 60, 40)
        circle(60, 94, 6, fill=True)
        arc(60, 52, 22, 200, 340)
        arc(60, 46, 34, 205, 335)
        return
    if code == "pc_sleep":
        circle(52, 64, 28, fill=True)
        draw.ellipse([p(58, 36), p(108, 96)], fill=INK)
        return
    if code == "pc_hibernate":
        draw.text(p(38, 48), "z", font=_font(int(28 * s), bold=True), fill=color)
        draw.text(p(58, 28), "z", font=_font(int(20 * s), bold=True), fill=color)
        return
    circle(60, 60, 36)
    line(28, 62, 44, 62, 54, 34, 70, 82, 80, 62, 92, 62)


def _fade(color, alpha: int):
    return (color[0], color[1], color[2], alpha)


def _fit_title(draw: ImageDraw.ImageDraw, title: str, max_width: int | None = None):
    max_width = 260 * SCALE if max_width is None else max_width
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

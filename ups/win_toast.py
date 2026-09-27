"""Always-on-top HUD cards and the tray-side notice inbox."""

from __future__ import annotations

import queue
import threading
from datetime import datetime
from io import BytesIO

_JOBS: queue.Queue = queue.Queue()
_STARTED = False
_LOCK = threading.Lock()

INBOX_W = 380
INBOX_HEAD = 50
INBOX_ROW = 52
INBOX_MAX_H = 360

_BG = "#0b1218"
_LINE = "#24343d"
_CYAN = "#4de2ff"
_TEXT = "#e7f6ff"
_MUTED = "#7f9aaa"
_GOLD = "#e0b06a"
_BAD = "#ffb1a8"
_OK = "#7cf0c0"


def show_card(png: bytes, title: str = "", message: str = "", on_click=None) -> bool:
    if not png:
        return False
    try:
        import tkinter  # noqa: F401
        from PIL import Image, ImageTk  # noqa: F401
    except ImportError:
        return False
    _JOBS.put({"kind": "card", "png": png, "on_click": on_click})
    _ensure_worker()
    return True


def toggle_inbox(items, on_open=None, on_clear=None) -> bool:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    _JOBS.put({
        "kind": "inbox",
        "items": list(items or []),
        "on_open": on_open,
        "on_clear": on_clear,
    })
    _ensure_worker()
    return True


def close_inbox() -> None:
    if not _STARTED:
        return
    _JOBS.put({"kind": "inbox_close"})


def inbox_size(count: int) -> tuple[int, int]:
    rows = max(1, min(int(count or 0), 6))
    return INBOX_W, INBOX_HEAD + rows * INBOX_ROW


def notice_tone(item: dict | None = None) -> str:
    item = item or {}
    code = str(item.get("code") or "")
    level = str(item.get("level") or "")
    if level == "bad" or "crit" in code or code in {"fault", "pc_off", "battery_loose"}:
        return "bad"
    if level == "ok" or code.endswith("_clear"):
        return "ok"
    return "warn"


def notice_clock(ts) -> str:
    try:
        value = float(ts or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return "--:--:--"
    return datetime.fromtimestamp(value).strftime("%H:%M:%S")


def _ensure_worker() -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
    threading.Thread(target=_loop, name="ups-toast-card", daemon=True).start()


def _loop() -> None:
    import tkinter as tk
    from PIL import Image, ImageTk

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-toolwindow", True)
    except tk.TclError:
        pass
    shown = {"win": None, "photo": None, "inbox": None}

    def close_card() -> None:
        win = shown["win"]
        shown["win"] = None
        shown["photo"] = None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def close_inbox_win() -> None:
        win = shown["inbox"]
        shown["inbox"] = None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def open_card(png: bytes, on_click) -> None:
        close_card()
        image = Image.open(BytesIO(png)).convert("RGB")
        width, height = _fit(image.size)
        if image.size != (width, height):
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
            win.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        photo = ImageTk.PhotoImage(image, master=win)
        label = tk.Label(win, image=photo, borderwidth=0, highlightthickness=0)
        label.image = photo
        label.pack()
        win.update_idletasks()
        x, y = _anchor(win, width, height)
        win.geometry(f"{width}x{height}+{x}+{y}")

        def click(_event=None) -> None:
            close_card()
            if on_click:
                try:
                    on_click()
                except Exception:
                    pass

        label.bind("<Button-1>", click)
        win.bind("<Escape>", lambda _event: close_card())
        win.after(8000, close_card)
        shown["win"] = win
        shown["photo"] = photo

    def toggle_inbox_win(items, on_open, on_clear) -> None:
        if shown["inbox"] is not None:
            close_inbox_win()
            return
        close_card()
        width, height = inbox_size(len(items))
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
            win.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        win.configure(bg=_CYAN, padx=1, pady=1)
        shell = tk.Frame(win, bg=_BG)
        shell.pack(fill="both", expand=True)
        _fill_inbox(shell, items, on_open, on_clear, close_inbox_win)
        win.update_idletasks()
        x, y = _anchor(win, width, height)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.bind("<Escape>", lambda _event: close_inbox_win())
        win.after(80, win.focus_force)
        shown["inbox"] = win

    def poll() -> None:
        try:
            while True:
                job = _JOBS.get_nowait()
                if isinstance(job, tuple):
                    open_card(job[0], job[1] if len(job) > 1 else None)
                    continue
                kind = job.get("kind")
                if kind == "card":
                    open_card(job.get("png"), job.get("on_click"))
                elif kind == "inbox":
                    toggle_inbox_win(job.get("items") or [], job.get("on_open"), job.get("on_clear"))
                elif kind == "inbox_close":
                    close_inbox_win()
        except queue.Empty:
            pass
        root.after(120, poll)

    poll()
    root.mainloop()


def _fill_inbox(shell, items, on_open, on_clear, close_fn) -> None:
    import tkinter as tk

    header = tk.Frame(shell, bg=_BG)
    header.pack(fill="x", padx=12, pady=(10, 6))
    tk.Label(
        header,
        text="Область уведомлений",
        bg=_BG,
        fg=_CYAN,
        font=("Segoe UI", 9),
        anchor="w",
    ).pack(side="left")

    def do_clear() -> None:
        if on_clear:
            try:
                on_clear()
            except Exception:
                pass
        for child in list(body.winfo_children()):
            child.destroy()
        _rows(body, [], on_open, close_fn)

    tk.Button(
        header,
        text="×",
        command=close_fn,
        bg=_BG,
        fg=_TEXT,
        activebackground=_BG,
        activeforeground=_CYAN,
        bd=0,
        font=("Segoe UI", 12),
        cursor="hand2",
        padx=6,
    ).pack(side="right")
    tk.Button(
        header,
        text="Очистить",
        command=do_clear,
        bg=_BG,
        fg=_MUTED,
        activebackground=_BG,
        activeforeground=_TEXT,
        bd=0,
        font=("Segoe UI", 8),
        cursor="hand2",
    ).pack(side="right", padx=(0, 8))

    body = tk.Frame(shell, bg=_BG)
    body.pack(fill="both", expand=True, padx=12, pady=(0, 8))
    _rows(body, items, on_open, close_fn)


def _rows(body, items, on_open, close_fn) -> None:
    import tkinter as tk

    if not items:
        tk.Label(body, text="Пока пусто.", bg=_BG, fg=_MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=8)
        return
    canvas = tk.Canvas(body, bg=_BG, highlightthickness=0, bd=0)
    scroll = tk.Scrollbar(body, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=_BG)
    inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw", width=INBOX_W - 36)
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side="left", fill="both", expand=True)
    if len(items) > 6:
        scroll.pack(side="right", fill="y")
    for item in items:
        _row(inner, item, on_open, close_fn)


def _row(parent, item, on_open, close_fn) -> None:
    import tkinter as tk

    tone = notice_tone(item)
    color = {"bad": _BAD, "ok": _OK}.get(tone, _GOLD)
    row = tk.Frame(parent, bg=_BG, cursor="hand2")
    row.pack(fill="x", pady=1)
    tk.Frame(row, bg=_LINE, height=1).pack(fill="x")
    tk.Label(row, text=notice_clock(item.get("t")), bg=_BG, fg=_MUTED, font=("Consolas", 8), anchor="w").pack(fill="x", pady=(6, 0))
    tk.Label(
        row,
        text=str(item.get("title") or "Событие UPS"),
        bg=_BG,
        fg=color,
        font=("Segoe UI", 10, "bold"),
        anchor="w",
        wraplength=INBOX_W - 48,
        justify="left",
    ).pack(fill="x", pady=(0, 6))

    def pick(_event=None) -> None:
        close_fn()
        if on_open:
            try:
                on_open()
            except Exception:
                pass

    for widget in (row, *row.winfo_children()):
        widget.bind("<Button-1>", pick)


def _fit(size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    target = 440
    if width <= target * 1.2:
        return width, height
    scale = target / width
    return max(280, int(width * scale)), max(140, int(height * scale))


def _anchor(root, width: int, height: int) -> tuple[int, int]:
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    return max(16, screen_w - width - 24), max(16, screen_h - height - 72)

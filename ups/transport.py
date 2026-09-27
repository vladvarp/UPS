"""Talk to a Mega(USB) UPS, a serial Megatec UPS, or a built-in demo."""

from __future__ import annotations

import ctypes
import re
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

from ups.protocol import ProtocolError, parse_q1, plausible

# Reading one of these HID string indexes is the Megatec command.
# Other indexes exist and some of them shut the output down, so the
# set is closed on purpose.
COMMANDS = {
    "Q1": 0x03,
    "T": 0x04,
    "TL": 0x05,
    "Q": 0x07,
    "C": 0x0B,
    "I": 0x0C,
    "F": 0x0D,
}

# Only Q1 on the hot path. Index 0xF3 (BL) and frequent I/F reads
# stall this bridge after a few ticks and then it repeats one frame.
POLL_CYCLE = ("Q1",)
# UPSmart asks for exactly this many bytes. Larger requests are known to
# hang some revisions of the chip.
REPLY_BYTES = 100

# USB ids that speak Megatec Q1. 0001:0000 is the one UPSmart calls Mega(USB).
KNOWN_IDS = {
    (0x0001, 0x0000),
    (0x0665, 0x5161),
    (0x06DA, 0x0002),
    (0x06DA, 0x0003),
    (0x06DA, 0x0004),
    (0x06DA, 0x0005),
    (0x06DA, 0x0201),
    (0x06DA, 0x0601),
    (0x0F03, 0x0001),
    (0x05B8, 0x0000),
    (0xFFFF, 0x0000),
    (0x0925, 0x1234),
    (0x1CB0, 0x0035),
    (0x0590, 0x00B7),
}

CREATE_NO_WINDOW = 0x08000000
ERROR_SHARING_VIOLATION = 32


class TransportError(Exception):
    pass


class DeviceBusy(TransportError):
    pass


@dataclass
class FoundDevice:
    path: str
    vendor_id: int
    product_id: int
    product: str
    manufacturer: str
    busy: bool = False


class MegaHidTransport:
    """Windows HID string-descriptor transport used by UPSmart's Mega(USB)."""

    kind = "mega-usb"

    def __init__(self, handle, found: FoundDevice):
        self._handle = handle
        self.found = found
        self._step = 0

    @property
    def label(self) -> str:
        return f"Mega USB {self.found.vendor_id:04X}:{self.found.product_id:04X}"

    def command(self, name: str, retries: int = 2) -> str:
        """Send one Megatec command. 'UPS No Ack' means the UPS did not answer."""
        index = COMMANDS.get(name)
        if index is None:
            raise TransportError(f"неизвестная команда {name}")
        last = ""
        for attempt in range(max(1, retries)):
            if attempt:
                time.sleep(0.6)
            # Instant commands (T/Q/C) often reply "UPS No Ack" in ~50 ms.
            # Do not sit on the HID handle for 3 seconds — that freezes the page.
            last = _hid_indexed_string(self._handle, index, timeout=1.0)
            if "no ack" not in last.lower():
                return last.strip()
            if name in {"T", "Q", "C"}:
                return last.strip() or "UPS No Ack"
        raise TransportError("ИБП не принял команду")

    def poll_step(self) -> tuple[str, str]:
        """Run the next read of the UPSmart cycle. Returns (command, reply)."""
        name = POLL_CYCLE[self._step % len(POLL_CYCLE)]
        self._step += 1
        reply = _hid_indexed_string(self._handle, COMMANDS[name], timeout=3.0).strip()
        return name, reply

    def query_q1(self) -> str:
        raw = _hid_indexed_string(self._handle, COMMANDS["Q1"], timeout=3.0).strip()
        if "no ack" in raw.lower():
            raise TransportError("ИБП не принял команду")
        status = parse_q1(raw)
        if not plausible(status):
            raise ProtocolError("пустой кадр")
        return raw

    def hard_reset(self) -> None:
        """Re-plug the USB bridge so it talks to the UPS again."""
        self.close()
        restart_mega_usb()

    def close(self) -> None:
        if self._handle:
            _kernel32().CloseHandle(self._handle)
            self._handle = None


class SerialTransport:
    kind = "serial"

    def __init__(self, ser, port: str, baud: int):
        self._ser = ser
        self.port = port
        self.baud = baud

    @property
    def label(self) -> str:
        return f"{self.port} · {self.baud}"

    def command(self, name: str, retries: int = 2) -> str:
        del retries
        if name not in COMMANDS:
            raise TransportError(f"неизвестная команда {name}")
        return self._exchange(f"{name}\r".encode("ascii"))

    def poll_step(self) -> tuple[str, str]:
        return "Q1", self.query_q1()

    def query_q1(self) -> str:
        reply = self.command("Q1")
        parse_q1(reply)
        return reply

    def hard_reset(self) -> None:
        return None

    def _exchange(self, payload: bytes) -> str:
        self._ser.reset_input_buffer()
        self._ser.write(payload)
        self._ser.flush()
        buf = bytearray()
        deadline = time.time() + 1.4
        while time.time() < deadline:
            chunk = self._ser.read(64)
            if chunk:
                buf.extend(chunk)
                if b"\r" in buf and (b"(" in buf or len(buf) > 8):
                    break
            elif buf:
                break
        text = buf.decode("ascii", "ignore")
        if not text:
            raise TransportError("порт не ответил")
        return text

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


class DemoSource:
    """A quiet imitation of the readings on the user's screen, for preview."""

    kind = "demo"

    def __init__(self) -> None:
        self.outage = False
        self.battery_v = 26.6
        self._phase = 0.0

    @property
    def label(self) -> str:
        return "Демонстрация"

    def command(self, name: str, retries: int = 2) -> str:
        del retries
        if name == "T":
            return "demo test"
        if name == "Q":
            return "demo beeper"
        return "demo"

    def poll_step(self) -> tuple[str, str]:
        return "Q1", self.query_q1()

    def hard_reset(self) -> None:
        return None

    def query_q1(self) -> str:
        self._phase += 0.35
        wobble = ((self._phase * 1.7) % 1.0 - 0.5) * 0.6
        load = 45 + ((self._phase * 0.37) % 5) - 2
        if self.outage:
            self.battery_v = max(21.4, self.battery_v - 0.01)
            flags = "10001000"
            inp = 0.0
            out = 220.0 + wobble
            freq = 50.0
        else:
            self.battery_v += (26.6 - self.battery_v) * 0.05
            flags = "00001000"
            inp = 216.0 + wobble
            out = 215.0 + wobble * 0.4
            freq = 49.6 + wobble * 0.05
        temp = 29.0 + wobble * 0.1
        return (
            f"({inp:05.1f} 189.0 {out:05.1f} {load:03.0f} "
            f"{freq:04.1f} {self.battery_v:04.1f} {temp:04.1f} {flags}"
        )

    def close(self) -> None:
        return None


def upsmart_running() -> bool:
    if sys.platform != "win32":
        return False
    import subprocess

    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq UPSmart.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "UPSmart.exe" in (result.stdout or "")


def list_serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = []
    for port in list_ports.comports():
        ports.append(
            {
                "device": port.device,
                "description": port.description or "",
                "hwid": port.hwid or "",
            }
        )
    return ports


def scan_hid() -> list[FoundDevice]:
    """List Megatec HID candidates without opening them.

    Opening every HID interface would poke mice and keyboards. The vendor
    and product ids are already in the device path.
    """
    if sys.platform != "win32":
        return []
    found: list[FoundDevice] = []
    seen: set[tuple[int, int, str]] = set()
    for path in _hid_paths():
        match = re.search(r"vid_([0-9a-f]{4})&pid_([0-9a-f]{4})", path, re.I)
        if not match:
            continue
        vid = int(match.group(1), 16)
        pid = int(match.group(2), 16)
        if (vid, pid) not in KNOWN_IDS:
            continue
        key = (vid, pid, path.lower())
        if key in seen:
            continue
        seen.add(key)
        found.append(FoundDevice(path, vid, pid, "", ""))
    found.sort(key=lambda item: (item.vendor_id != 0x0001, item.vendor_id, item.product_id))
    return found


def restart_mega_usb() -> bool:
    """Ask Windows to unplug and plug the Mega USB device again.

    UPSmart does this (it runs as administrator). The bridge chip then
    starts fetching fresh Q1 frames instead of repeating one cached line.
    """
    if sys.platform != "win32":
        return False
    devices = scan_hid()
    if not devices:
        return False
    cfg = ctypes.WinDLL("cfgmgr32")
    locate = cfg.CM_Locate_DevNodeW
    locate.argtypes = [ctypes.POINTER(wintypes.DWORD), ctypes.c_wchar_p, wintypes.ULONG]
    parent = cfg.CM_Get_Parent
    parent.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.ULONG]
    disable = cfg.CM_Disable_DevNode
    disable.argtypes = [wintypes.DWORD, wintypes.ULONG]
    enable = cfg.CM_Enable_DevNode
    enable.argtypes = [wintypes.DWORD, wintypes.ULONG]
    inst = wintypes.DWORD(0)
    hid_id = _instance_id_from_hid_path(devices[0].path)
    if locate(ctypes.byref(inst), hid_id, 0) != 0:
        return False
    usb = wintypes.DWORD(0)
    if parent(ctypes.byref(usb), inst, 0) != 0:
        usb = inst
    if disable(usb, 0) != 0:
        return False
    time.sleep(1.6)
    if enable(usb, 0) != 0:
        enable(usb, 0)
    time.sleep(2.2)
    return True


def _instance_id_from_hid_path(path: str) -> str:
    body = path.split("#{")[0]
    for prefix in ("\\\\?\\", "\\\\.\\"):
        if body.lower().startswith(prefix):
            body = body[len(prefix):]
    parts = body.split("#")
    if len(parts) >= 3:
        return f"{parts[0].upper()}\\{parts[1].upper()}\\{parts[2]}"
    return body.upper()


def open_mega(found: FoundDevice) -> MegaHidTransport:
    handle, error = _open_hid(found.path)
    if handle is None:
        if error == ERROR_SHARING_VIOLATION:
            raise DeviceBusy(found.path)
        raise TransportError(f"не удалось открыть HID (код {error})")
    found.product = _hid_text(handle, "product")
    found.manufacturer = _hid_text(handle, "manufacturer")
    return MegaHidTransport(handle, found)


def open_serial(port: str) -> SerialTransport:
    import serial

    last_error: Exception | None = None
    for baud in (2400, 9600):
        ser = serial.Serial(
            port,
            baudrate=baud,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.4,
            write_timeout=0.8,
        )
        transport = SerialTransport(ser, port, baud)
        try:
            transport.query_q1()
            return transport
        except Exception as exc:
            last_error = exc
            transport.close()
    raise TransportError(f"{port} не ответил как ИБП Megatec ({last_error})")


def _likely_serial(description: str, hwid: str) -> bool:
    blob = f"{description} {hwid}".lower()
    if "bluetooth" in blob or "bthenum" in blob:
        return False
    keys = (
        "ch340",
        "ch341",
        "cp210",
        "prolific",
        "ftdi",
        "usb serial",
        "usb-serial",
        "qinheng",
        "silicon labs",
    )
    return any(key in blob for key in keys)


def probe_serial(preferred: str = "") -> SerialTransport | None:
    if preferred:
        return open_serial(preferred)
    for port in list_serial_ports():
        if not _likely_serial(port["description"], port["hwid"]):
            continue
        try:
            return open_serial(port["device"])
        except TransportError:
            continue
    return None


# --- Windows HID plumbing ---------------------------------------------------

def _kernel32():
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.CreateFileW.restype = ctypes.c_void_p
    lib.CloseHandle.argtypes = [ctypes.c_void_p]
    return lib


def _hid():
    lib = ctypes.WinDLL("hid", use_last_error=True)
    lib.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.HidD_GetAttributes.restype = ctypes.c_bool
    lib.HidD_GetIndexedString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.c_ulong,
    ]
    lib.HidD_GetIndexedString.restype = ctypes.c_bool
    lib.HidD_GetProductString.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
    lib.HidD_GetProductString.restype = ctypes.c_bool
    lib.HidD_GetManufacturerString.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
    lib.HidD_GetManufacturerString.restype = ctypes.c_bool
    return lib


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _INTERFACE(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", _GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class _ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", ctypes.c_ulong),
        ("VendorID", ctypes.c_ushort),
        ("ProductID", ctypes.c_ushort),
        ("VersionNumber", ctypes.c_ushort),
    ]


_HID_GUID = _GUID(
    0x4D1E55B2,
    0xF16F,
    0x11CF,
    (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30),
)


def _hid_paths() -> list[str]:
    setup = ctypes.WinDLL("setupapi", use_last_error=True)
    setup.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setup.SetupDiEnumDeviceInterfaces.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        ctypes.POINTER(_INTERFACE),
    ]
    setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_INTERFACE),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    setup.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    flags = 0x2 | 0x10  # present + device interface
    collection = setup.SetupDiGetClassDevsW(ctypes.byref(_HID_GUID), None, None, flags)
    paths: list[str] = []
    index = 0
    while True:
        data = _INTERFACE()
        data.cbSize = ctypes.sizeof(data)
        ok = setup.SetupDiEnumDeviceInterfaces(
            collection, None, ctypes.byref(_HID_GUID), index, ctypes.byref(data)
        )
        if not ok:
            break
        needed = wintypes.DWORD(0)
        setup.SetupDiGetDeviceInterfaceDetailW(
            collection, ctypes.byref(data), None, 0, ctypes.byref(needed), None
        )
        buf = ctypes.create_string_buffer(needed.value)
        header = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = header
        if setup.SetupDiGetDeviceInterfaceDetailW(
            collection, ctypes.byref(data), buf, needed, None, None
        ):
            path = ctypes.cast(
                ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD),
                ctypes.c_wchar_p,
            ).value
            if path:
                paths.append(path)
        index += 1
    setup.SetupDiDestroyDeviceInfoList(collection)
    return paths


def _open_hid(path: str):
    kernel = _kernel32()
    handle = kernel.CreateFileW(
        path,
        0x80000000 | 0x40000000,  # generic read | write
        0x1 | 0x2,  # share read | write
        None,
        3,  # open existing
        0,
        None,
    )
    if not handle or handle == ctypes.c_void_p(-1).value:
        return None, ctypes.get_last_error()
    return handle, 0


def _attributes(handle) -> tuple[int, int] | None:
    attrs = _ATTRIBUTES()
    attrs.Size = ctypes.sizeof(attrs)
    if not _hid().HidD_GetAttributes(handle, ctypes.byref(attrs)):
        return None
    return attrs.VendorID, attrs.ProductID


def _hid_text(handle, kind: str) -> str:
    buf = ctypes.create_unicode_buffer(128)
    fn = {
        "product": _hid().HidD_GetProductString,
        "manufacturer": _hid().HidD_GetManufacturerString,
    }[kind]
    if not fn(handle, buf, ctypes.sizeof(buf)):
        return ""
    return buf.value


def _hid_indexed_string(handle, index: int, timeout: float = 3.0) -> str:
    """Read one HID string, but do not let a stalled USB chip freeze the page."""
    box: dict = {}

    def work() -> None:
        buf = ctypes.create_unicode_buffer(REPLY_BYTES // 2)
        ok = _hid().HidD_GetIndexedString(handle, ctypes.c_ulong(index), buf, REPLY_BYTES)
        if ok:
            box["text"] = buf.value
        else:
            box["err"] = ctypes.get_last_error()

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        kernel = _kernel32()
        kernel.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel.CancelIoEx.restype = ctypes.c_bool
        kernel.CancelIoEx(handle, None)
        worker.join(1.0)
        raise TransportError("ИБП не ответил вовремя")
    if "err" in box:
        raise TransportError(f"ИБП не ответил на команду (код {box['err']})")
    return box.get("text", "")

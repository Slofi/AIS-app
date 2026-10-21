"""RTL-SDR status for the Cyberdeck apps (S466, 2026-10-21).

CANONICAL COPY: Projects/cyberdeck/launcher/rtlsdr_status.py — identical copies live in
adsb-app, ais-app, sonde and casper. Change this one, then copy it to the four apps.

Each app decides "working" from its own decoder (only it knows the decoder); this module
gives the shared parts: is the dongle plugged in, who holds it, and the result format.

States: missing · busy · idle · starting · stalled · ok
"""
import glob
import json
import os
import urllib.request

# RTL2832U dongles (RTL-SDR Blog V3/V4 = 0bda:2838; some generics = 0bda:2832)
RTLSDR_IDS = {("0bda", "2838"), ("0bda", "2832")}
LAUNCHER_SDR_URL = "http://127.0.0.1:8080/sdr"


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def present():
    """True when an RTL-SDR dongle is on the USB bus (sysfs read, no sudo)."""
    for dev in glob.glob("/sys/bus/usb/devices/*"):
        if (_read(os.path.join(dev, "idVendor")), _read(os.path.join(dev, "idProduct"))) in RTLSDR_IDS:
            return True
    return False


def holder():
    """Program holding the dongle according to the launcher: '' = free,
    None = launcher unreachable (unknown)."""
    try:
        with urllib.request.urlopen(LAUNCHER_SDR_URL, timeout=1.5) as r:
            d = json.load(r)
        return "" if d.get("free") else (d.get("user") or "another program")
    except Exception:
        return None


def result(state, text):
    return {"state": state, "text": text}


def not_running(own_names=()):
    """Status when this app's decoder is not running (dongle known to be present)."""
    h = holder()
    if h is None:   # launcher unreachable: do not claim "free" without knowing (S466 sweep)
        return result("idle", "decoder stopped · SDR owner unknown")
    if h and h not in own_names:
        return result("busy", f"SDR busy · {h}")
    return result("idle", "SDR free · decoder stopped")


def traffic_text(per_min, unit="msg"):
    """Text for the working state; a quiet radio is not a fault (Filip, S466)."""
    if per_min:
        plural = "" if per_min == 1 or unit == "msg" else "s"
        return f"SDR working · {per_min} {unit}{plural}/min"
    return "SDR working · nothing heard"

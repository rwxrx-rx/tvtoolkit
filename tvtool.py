#!/usr/bin/env python3
"""tvtool - debloat an Android TV / Google TV over Wi-Fi ADB and install a minimal home screen.

Designed for Termux on an Android phone (pkg install python android-tools), but works on any
machine with Python 3.8+ and either the `adb` binary or the `adb-shell` pip package.

Rules this tool follows:
  * nothing is ever uninstalled; packages are only disabled (pm disable-user --user 0)
  * a full backup is taken before the first change
  * every change is logged, and restore.sh / `tvtool.py restore` undoes all of it
  * after every batch it takes a screenshot from the TV and checks the TV is still healthy;
    an unhealthy batch is rolled back automatically
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

LAUNCHER_PKG = "com.tvdock.home"
LAUNCHER_ACT = LAUNCHER_PKG + "/.MainActivity"
SETUPWRAITH = "com.google.android.tungsten.setupwraith"

# ---------------------------------------------------------------------------------------------
# Package catalogue. Only packages that are actually installed and enabled are ever offered.
# ---------------------------------------------------------------------------------------------
ADS_TELEMETRY = [  # offered as one group, default yes
    ("com.tcl.waterfall.overseas", "TCL Channel / sponsored content rows"),
    ("com.tcl.channelplus", "TCL ad-supported free channels"),
    ("com.tcl.useragreement", "TCL privacy / user-agreement pop-ups"),
    ("com.google.android.tv.bugreportsender", "Sends bug reports to Google"),
    ("com.google.android.feedback", "Google feedback / crash reporting agent"),
]
FEATURES = [  # asked one by one, default no
    ("com.tcl.miracast", "Miracast: mirror your phone screen to the TV"),
    ("com.tcl.keyhelp", "On-screen remote key help"),
    ("com.tcl.guard", "TCL Guard (cleaner / 'security' app)"),
    ("com.tcl.ttvs", "Unidentified TCL service (other owners report it as bloat)"),
    ("com.google.android.katniss", "Voice search / Assistant (the mic button on the remote)"),
    ("com.google.android.apps.tv.dreamx", "Google TV ambient-mode screensaver"),
    ("com.google.android.backdrop", "Android TV screensaver (Backdrop)"),
]
STREAMING = [  # preinstalled apps you may not use; asked one by one, default no
    ("Netflix", "com.netflix.ninja"),
    ("Prime Video", "com.amazon.amazonvideo.livingroom"),
    ("Disney+", "com.disney.disneyplus"),
    ("Max", "com.wbd.stream"),
    ("Apple TV", "com.apple.atve.androidtv.appletv"),
    ("Spotify", "com.spotify.tv.android"),
    ("YouTube Music", "com.google.android.youtube.tvmusic"),
    ("YouTube TV (live)", "com.google.android.youtube.tvunplugged"),
    ("Plex", "com.plexapp.android"),
    ("Pluto TV", "tv.pluto.android"),
    ("Tubi", "com.tubitv"),
    ("Hulu", "com.hulu.livingroomplus"),
    ("Peacock", "com.peacocktv.peacockandroid"),
    ("Paramount+", "com.cbs.ott"),
]
STOCK_LAUNCHERS = [  # only offered AFTER the new home screen passed the Home-button test
    "com.google.android.apps.tv.launcherx",
    "com.google.android.tvlauncher",
    "com.google.android.leanbacklauncher",
    "com.google.android.tvrecommendations",
    "com.google.android.leanbacklauncher.recommendations",
]
KNOWN_APPS = [  # for the dock picker
    ("YouTube", "com.google.android.youtube.tv"),
    ("Netflix", "com.netflix.ninja"),
    ("Prime Video", "com.amazon.amazonvideo.livingroom"),
    ("Disney+", "com.disney.disneyplus"),
    ("Apple TV", "com.apple.atve.androidtv.appletv"),
    ("Max", "com.wbd.stream"),
    ("Spotify", "com.spotify.tv.android"),
    ("Plex", "com.plexapp.android"),
    ("YouTube Music", "com.google.android.youtube.tvmusic"),
    ("Kodi", "org.xbmc.kodi"),
    ("SmartTube", "com.teamsmart.videomanager.tv"),
    ("VLC", "org.videolan.vlc"),
]

# Never offered, whatever a heuristic says. These are the TV's plumbing.
PROTECT_EXACT = {
    "android", "com.android.systemui", "com.android.tv.settings", "com.android.shell",
    "com.android.bluetooth", "com.android.location.fused", "com.android.vending",
    "com.android.packageinstaller", "com.google.android.packageinstaller",
    "com.android.permissioncontroller", "com.google.android.permissioncontroller",
    "com.android.certinstaller", "com.android.keychain", "com.android.captiveportallogin",
    "com.android.managedprovisioning", "com.android.inputdevices", "com.android.webview",
    "com.google.android.webview", "com.google.android.gms", "com.google.android.gsf",
    "com.google.android.tv.remote.service", "com.google.android.tv",
    "com.tcl.suspension",  # the Inputs/Source menu, disabling it loses HDMI switching
    "com.tcl.tv", "com.tcl.tvinput", "com.tcl.tcl_bt_rcu_service", "com.tcl.autopair",
    "com.tcl.initsetup", "com.tcl.factory.view",
    LAUNCHER_PKG,
}
PROTECT_PREFIX = (
    "com.android.providers.", "com.android.internal.", "com.android.server.", "com.android.tv.",
    "com.android.inputmethod.", "com.google.android.inputmethod.", "com.android.networkstack",
    "com.google.android.networkstack", "com.android.wifi", "com.google.android.wifi",
    "com.android.cellbroadcast", "com.google.android.ext.", "com.android.modulemetadata",
    "com.google.android.modulemetadata", "com.google.mainline", "com.android.cts",
    "com.android.settings", "com.android.se", "com.android.nfc", "com.android.phone",
    "com.android.emergency", "com.android.ons", "com.android.carrierconfig",
)
HEURISTIC = re.compile(
    r"(analytic|telemetry|adservice|\.ads?(\.|$)|advert|tracking|statistic|promo|sponsor|"
    r"datacollect|logservice|uploader|dataupload|usagereport)", re.I)

HOWTO_ENABLE = """
Turn on ADB on the TV first:
  1. Settings > Device Preferences > About > tap 'Build' 7 times (Developer options unlocks).
  2. Settings > Device Preferences > Developer options > turn on 'USB debugging'.
     Newer Google TV models call it 'Wireless debugging'. If you see a pairing code screen,
     note the IP:port and the pairing code; this tool will ask for them.
  3. Keep the TV on, awake and on the same Wi-Fi as this phone.
"""

ASSUME_YES = False


def say(msg=""):
    print(msg, flush=True)


def ask(question, default=True):
    if ASSUME_YES:
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            a = input(f"{question} {suffix} ").strip().lower()
        except EOFError:
            return default
        if not a:
            return default
        if a in ("y", "yes"):
            return True
        if a in ("n", "no"):
            return False


def ask_text(question, default=""):
    if ASSUME_YES:
        return default
    try:
        a = input(f"{question} ").strip()
    except EOFError:
        return default
    return a or default


def now():
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def slug(s):
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:40] or "x"


def parse_pkgs(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            rest = line[8:]
            out.append(rest.rsplit("=", 1)[-1] if "=" in rest else rest)
    return out


def is_protected(pkg):
    return pkg in PROTECT_EXACT or pkg.startswith(PROTECT_PREFIX)


# ---------------------------------------------------------------------------------------------
# ADB transports. Both expose: sh(cmd), screencap(), push(local, remote), install(apk), close().
# ---------------------------------------------------------------------------------------------
class AdbBinary:
    name = "adb"

    def __init__(self, serial):
        self.adb = shutil.which("adb")
        self.serial = serial

    def _run(self, args, timeout=60, binary=False):
        p = subprocess.run([self.adb, "-s", self.serial] + args, capture_output=True, timeout=timeout)
        if binary:
            return p.stdout
        return (p.stdout + p.stderr).decode("utf-8", "replace").replace("\r", "")

    def connect(self):
        out = subprocess.run([self.adb, "connect", self.serial], capture_output=True, timeout=30)
        text = (out.stdout + out.stderr).decode("utf-8", "replace")
        return "connected" in text, text.strip()

    def state(self):
        try:
            return self._run(["get-state"], 15).strip()
        except Exception as e:  # noqa
            return f"error: {e}"

    def sh(self, cmd, timeout=60):
        return self._run(["shell", cmd], timeout)

    def screencap(self):
        return self._run(["exec-out", "screencap", "-p"], 60, binary=True)

    def push(self, local, remote):
        return self._run(["push", local, remote], 300)

    def install(self, apk):
        out = self._run(["install", "-r", apk], 300)
        if "Success" not in out:
            raise RuntimeError("install failed: " + out.strip())
        return out

    def reboot(self):
        self._run(["reboot"], 20)

    def close(self):
        pass


class AdbPure:
    """Fallback using the adb-shell package (no adb binary needed, e.g. when macOS blocks it)."""
    name = "adb-shell"

    def __init__(self, serial, keypath):
        from adb_shell.adb_device import AdbDeviceTcp
        from adb_shell.auth.keygen import keygen
        from adb_shell.auth.sign_pythonrsa import PythonRSASigner
        self.serial = serial
        host, port = serial.rsplit(":", 1)
        if not os.path.exists(keypath):
            keygen(keypath)
        with open(keypath) as f:
            priv = f.read()
        with open(keypath + ".pub") as f:
            pub = f.read()
        self.signer = PythonRSASigner(pub, priv)
        self.AdbDeviceTcp = AdbDeviceTcp
        self.host, self.port = host, int(port)
        self.d = None

    def connect(self):
        try:
            self.d = self.AdbDeviceTcp(self.host, self.port, default_transport_timeout_s=20)
            self.d.connect(rsa_keys=[self.signer], auth_timeout_s=60)
            return True, "connected"
        except Exception as e:  # noqa
            return False, str(e)

    def state(self):
        try:
            return "device" if self.d and self.d.available else "offline"
        except Exception:
            return "offline"

    def sh(self, cmd, timeout=60):
        return self.d.shell(cmd, read_timeout_s=timeout, timeout_s=timeout).replace("\r", "")

    def screencap(self):
        try:
            return self.d.exec_out("screencap -p", decode=False, read_timeout_s=60)
        except Exception:
            data = self.d.shell("screencap -p", decode=False, read_timeout_s=60)
            return data.replace(b"\r\n", b"\n")

    def push(self, local, remote):
        self.d.push(local, remote)

    def install(self, apk):
        remote = "/data/local/tmp/tvtool.apk"
        self.d.push(apk, remote)
        out = self.sh(f"pm install -r {remote}", 300)
        self.sh(f"rm -f {remote}")
        if "Success" not in out:
            raise RuntimeError("install failed: " + out.strip())
        return out

    def reboot(self):
        try:
            self.d.shell("reboot", read_timeout_s=5, timeout_s=5)
        except Exception:
            pass

    def close(self):
        try:
            self.d.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------------------------
# Discovery and connection
# ---------------------------------------------------------------------------------------------
def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def scan_network(timeout=0.4):
    found = []
    ip = local_ip()
    if ip:
        base = ip.rsplit(".", 1)[0]

        def probe(host):
            try:
                with socket.create_connection((host, 5555), timeout):
                    return f"{host}:5555"
            except OSError:
                return None

        hosts = [f"{base}.{i}" for i in range(1, 255) if f"{base}.{i}" != ip]
        with cf.ThreadPoolExecutor(128) as ex:
            found = [r for r in ex.map(probe, hosts) if r]
    adb = shutil.which("adb")
    if adb:  # Wireless-debugging TVs advertise a random port over mDNS
        try:
            out = subprocess.run([adb, "mdns", "services"], capture_output=True, timeout=10).stdout.decode()
            for m in re.finditer(r"_adb-tls-connect\._tcp\.?\s+(\d+\.\d+\.\d+\.\d+:\d+)", out):
                if m.group(1) not in found:
                    found.append(m.group(1))
        except Exception:
            pass
    return found, ip


def pair_flow(args):
    adb = shutil.which("adb")
    if not adb:
        say("Pairing needs the adb binary (pkg install android-tools).")
        return
    addr = ask_text("Pairing IP:port shown on the TV (blank to skip):")
    if not addr:
        return
    code = ask_text("6-digit pairing code:")
    out = subprocess.run([adb, "pair", addr, code], capture_output=True, timeout=60)
    say((out.stdout + out.stderr).decode("utf-8", "replace").strip())


def pick_target(args):
    if args.ip:
        return args.ip if ":" in args.ip else args.ip + ":5555"
    say("Scanning your Wi-Fi for TVs with ADB on (port 5555 and mDNS)...")
    found, ip = scan_network()
    if ip:
        say(f"This phone: {ip}")
    if len(found) == 1:
        say(f"Found {found[0]}")
        return found[0]
    if len(found) > 1:
        for i, f in enumerate(found, 1):
            say(f"  {i}) {f}")
        n = ask_text("Which one is the TV? number:", "1")
        try:
            return found[int(n) - 1]
        except (ValueError, IndexError):
            pass
    say("No TV found automatically.")
    say(HOWTO_ENABLE)
    if ask("Does the TV show a pairing code (Wireless debugging)?", False):
        pair_flow(args)
    addr = ask_text("Type the TV address as IP or IP:port (TV: Settings > Network shows the IP):")
    if not addr:
        sys.exit("No address given.")
    return addr if ":" in addr else addr + ":5555"


class Ctx:
    pass


def connect(args):
    ctx = Ctx()
    ctx.args = args
    target = pick_target(args)
    ctx.serial = target
    ctx.work = os.path.join(args.workdir, target.replace(":", "_"))
    os.makedirs(os.path.join(ctx.work, "shots"), exist_ok=True)
    ctx.dev = None
    use_pure = args.pure or not shutil.which("adb")
    if use_pure:
        try:
            import adb_shell  # noqa: F401
        except ImportError:
            sys.exit("Neither adb nor adb-shell found. In Termux: pkg install android-tools\n"
                     "Elsewhere: pip install adb-shell")
        dev = AdbPure(target, os.path.join(args.workdir, "adbkey"))
    else:
        dev = AdbBinary(target)
    ok, msg = dev.connect()
    if not ok:
        say(msg)
        say(HOWTO_ENABLE)
        sys.exit("Could not connect.")
    ctx.dev = dev
    say(f"Connected via {dev.name}. If the TV shows 'Allow USB debugging?', pick Always allow.")
    deadline = time.time() + 90
    while time.time() < deadline:
        st = dev.state()
        if st == "device":
            break
        time.sleep(2)
    else:
        sys.exit("TV did not authorise this device in time (state: %s)." % dev.state())
    ctx.changes_path = os.path.join(ctx.work, "changes.json")
    ctx.changes = json.load(open(ctx.changes_path)) if os.path.exists(ctx.changes_path) else []
    ctx.report_path = os.path.join(ctx.work, "report.html")
    ctx.report = []
    ctx.shot_n = len([f for f in os.listdir(os.path.join(ctx.work, "shots")) if f.endswith(".png")])
    ctx.last_hash = None
    return ctx


def reconnect(ctx, timeout=180):
    say("Waiting for the TV to come back...")
    end = time.time() + timeout
    time.sleep(10)
    while time.time() < end:
        ok, _ = ctx.dev.connect()
        if ok and ctx.dev.state() == "device":
            time.sleep(8)
            return True
        time.sleep(5)
    return False


# ---------------------------------------------------------------------------------------------
# State: change log, screenshots, report
# ---------------------------------------------------------------------------------------------
def record(ctx, **change):
    change["time"] = dt.datetime.now().isoformat(timespec="seconds")
    ctx.changes.append(change)
    with open(ctx.changes_path, "w") as f:
        json.dump(ctx.changes, f, indent=1)
    write_restore_sh(ctx)


def shot(ctx, label):
    ctx.shot_n += 1
    try:
        data = ctx.dev.screencap()
    except Exception as e:  # noqa
        say(f"  [screenshot failed: {e}]")
        return False
    name = f"{ctx.shot_n:02d}-{slug(label)}.png"
    note = ""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        note = "not a PNG"
    elif len(data) < 12000:
        note = "looks blank/black (HDMI and DRM video often screenshot black)"
    h = hashlib.md5(data).hexdigest()
    if not note and h == ctx.last_hash:
        note = "identical to the previous screenshot"
    ctx.last_hash = h
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        with open(os.path.join(ctx.work, "shots", name), "wb") as f:
            f.write(data)
    ctx.report.append({"step": label, "file": name, "note": note,
                       "time": dt.datetime.now().strftime("%H:%M:%S")})
    write_report(ctx)
    say(f"  screenshot {name}" + (f"  [{note}]" if note else ""))
    return not note


def write_report(ctx):
    rows = []
    for r in ctx.report:
        img = f'<img src="shots/{html.escape(r["file"])}" width="420">' if r["file"] else ""
        note = f'<div class="n">{html.escape(r["note"])}</div>' if r["note"] else ""
        rows.append(f'<div class="c"><b>{html.escape(r["step"])}</b> <small>{r["time"]}</small>{note}<br>{img}</div>')
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>TV report</title><style>body{font:15px sans-serif;background:#111;color:#eee;margin:12px}"
            ".c{margin:0 0 18px}img{max-width:100%;border-radius:8px}.n{color:#f90}</style>"
            f"<h2>TV debloat report - {html.escape(ctx.serial)}</h2>" + "".join(rows))
    with open(ctx.report_path, "w") as f:
        f.write(page)


def write_restore_sh(ctx):
    lines = ["#!/bin/sh",
             f"# Restore for TV {ctx.serial}. Generated {dt.datetime.now():%Y-%m-%d %H:%M}.",
             "# Re-enables everything tvtool disabled and puts the old home screen back.",
             "# Nothing was ever uninstalled, so this fully reverts the changes.",
             "# Usage: sh restore.sh [ip:port] [--remove-launcher]",
             f'S="{ctx.serial}"; REMOVE=0',
             'for x in "$@"; do if [ "$x" = "--remove-launcher" ]; then REMOVE=1; else S="$x"; fi; done',
             'adb connect "$S" >/dev/null 2>&1',
             'a() { adb -s "$S" shell "$@"; }',
             "# Re-enable stock launchers first so the TV always has a home screen"]
    for pkg in STOCK_LAUNCHERS + [SETUPWRAITH]:
        lines.append(f"a pm enable {pkg} 2>/dev/null")
    for c in reversed(ctx.changes):
        t = c["type"]
        if t == "disable":
            lines.append(f"a pm enable {c['pkg']}")
        elif t == "setting":
            if c["old"] in (None, "null"):
                lines.append(f"a settings delete {c['ns']} {c['key']}")
            else:
                lines.append(f"a settings put {c['ns']} {c['key']} {c['old']}")
        elif t == "home" and c.get("old"):
            lines.append(f"a cmd package set-home-activity {c['old']}")
    lines += ['if [ "$REMOVE" = "1" ]; then adb -s "$S" uninstall %s; fi' % LAUNCHER_PKG,
              'echo "Done. Reboot the TV (hold power or: adb -s $S reboot) to finish."']
    path = os.path.join(ctx.work, "restore.sh")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o755)


# ---------------------------------------------------------------------------------------------
# Device helpers
# ---------------------------------------------------------------------------------------------
def getprop(dev, key):
    return dev.sh(f"getprop {key}").strip()


def resolve_home(dev):
    out = dev.sh("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                 "-c android.intent.category.HOME")
    comps = re.findall(r"^([\w.]+/[\w.$]+)\s*$", out, re.M)
    return comps[-1] if comps else None


def resumed(dev):
    out = dev.sh("dumpsys activity activities | grep -i resumed")
    m = re.search(r"([\w.]+)/([\w.$]+)", out)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def meminfo(dev):
    t = dev.sh("cat /proc/meminfo")

    def g(k):
        m = re.search(rf"{k}:\s+(\d+)", t)
        return int(m.group(1)) // 1024 if m else 0
    return {"total_mb": g("MemTotal"), "avail_mb": g("MemAvailable"), "free_mb": g("MemFree")}


def healthy(dev):
    try:
        if "ok" not in dev.sh("echo ok", 20):
            return False, "TV shell not responding"
    except Exception as e:  # noqa
        return False, f"TV not responding ({e})"
    home = resolve_home(dev)
    if not home:
        return False, "no home app resolves any more"
    return True, home


def installed(dev):
    return {
        "enabled": set(parse_pkgs(dev.sh("pm list packages -e"))),
        "disabled": set(parse_pkgs(dev.sh("pm list packages -d"))),
        "system": set(parse_pkgs(dev.sh("pm list packages -s"))),
        "user": set(parse_pkgs(dev.sh("pm list packages -3"))),
    }


def disable_pkg(ctx, pkg):
    out = ctx.dev.sh(f"pm disable-user --user 0 {pkg}", 30)
    ok = "new state: disabled" in out
    if ok:
        record(ctx, type="disable", pkg=pkg)
    else:
        say(f"  could not disable {pkg}: {out.strip()[:120]}")
    return ok


def enable_pkg(ctx, pkg):
    return "enabled" in ctx.dev.sh(f"pm enable {pkg}", 30)


def put_setting(ctx, ns, key, value):
    old = ctx.dev.sh(f"settings get {ns} {key}").strip()
    ctx.dev.sh(f"settings put {ns} {key} {value}")
    record(ctx, type="setting", ns=ns, key=key, old=None if old in ("", "null") else old, new=value)


# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------
def cmd_probe(ctx):
    d = ctx.dev
    pk = installed(d)
    info = {k: getprop(d, v) for k, v in {
        "brand": "ro.product.brand", "model": "ro.product.model", "device": "ro.product.device",
        "android": "ro.build.version.release", "sdk": "ro.build.version.sdk",
        "fingerprint": "ro.build.fingerprint"}.items()}
    allp = pk["enabled"] | pk["disabled"]
    info["ui"] = ("Google TV" if "com.google.android.apps.tv.launcherx" in allp
                  else "Android TV" if "com.google.android.tvlauncher" in allp
                  or "com.google.android.leanbacklauncher" in allp else "unknown launcher")
    info["home"] = resolve_home(d)
    info["wraith_present"] = SETUPWRAITH in allp
    say(f"TV: {info['brand']} {info['model']} ({info['device']}), Android {info['android']} "
        f"(SDK {info['sdk']}), {info['ui']}")
    say(f"Current home app: {info['home']}")
    if "tcl" not in (info["brand"] + info["model"]).lower():
        say("Note: this does not look like a TCL; the package lists are TCL-tuned, so expect fewer matches.")
    ctx.info = info
    return info


def cmd_backup(ctx):
    d = ctx.dev
    bdir = os.path.join(ctx.work, "backup-" + now())
    os.makedirs(bdir)
    say(f"Backing up to {bdir}")
    files = {
        "packages_all.txt": "pm list packages -u -f",
        "packages_enabled.txt": "pm list packages -e",
        "packages_disabled.txt": "pm list packages -d",
        "packages_system.txt": "pm list packages -s",
        "packages_user.txt": "pm list packages -3",
        "settings_global.txt": "settings list global",
        "settings_secure.txt": "settings list secure",
        "settings_system.txt": "settings list system",
        "props.txt": "getprop",
        "meminfo.txt": "cat /proc/meminfo",
        "home_resolve.txt": "cmd package resolve-activity --brief -a android.intent.action.MAIN "
                            "-c android.intent.category.HOME",
    }
    for name, cmd in files.items():
        with open(os.path.join(bdir, name), "w") as f:
            f.write(d.sh(cmd, 90))
    meta = {"serial": ctx.serial, "time": dt.datetime.now().isoformat(timespec="seconds"),
            "home": resolve_home(d), "mem": meminfo(d)}
    with open(os.path.join(bdir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    with open(os.path.join(ctx.work, "state.json"), "w") as f:
        json.dump({"backup": bdir, "mem_before": meta["mem"], "home_before": meta["home"]}, f)
    shot(ctx, "baseline before any change")
    write_restore_sh(ctx)
    say("Backup done.")
    return bdir


def ensure_backup(ctx):
    if not os.path.exists(os.path.join(ctx.work, "state.json")):
        cmd_backup(ctx)


def apply_batch(ctx, pkgs, label):
    done = []
    for p in pkgs:
        if disable_pkg(ctx, p):
            done.append(p)
    if not done:
        return True
    say(f"  disabled {len(done)}: " + ", ".join(done))
    time.sleep(3)
    shot(ctx, f"after {label}")
    ok, why = healthy(ctx.dev)
    if not ok:
        say(f"  !! TV looks unhealthy ({why}). Rolling this batch back.")
        for p in done:
            enable_pkg(ctx, p)
        ctx.changes = [c for c in ctx.changes if not (c["type"] == "disable" and c["pkg"] in done)]
        with open(ctx.changes_path, "w") as f:
            json.dump(ctx.changes, f, indent=1)
        write_restore_sh(ctx)
        return False
    return True


def cmd_debloat(ctx):
    d = ctx.dev
    ensure_backup(ctx)
    pk = installed(d)
    enabled = pk["enabled"]
    plan = []  # (pkg, desc)
    known = set()

    # 1. ads & telemetry
    ads = [(p, t) for p, t in ADS_TELEMETRY if p in enabled and not is_protected(p)]
    known |= {p for p, _ in ADS_TELEMETRY}
    if ads:
        say("\n== Ads, sponsored content and telemetry ==")
        for p, t in ads:
            say(f"  {p}\n      {t}")
        a = "y" if ASSUME_YES else ask_text("Disable all of these? [Y]es / [n]o / [p]ick one by one:", "y").lower()
        if a.startswith("y"):
            plan += ads
        elif a.startswith("p"):
            for p, t in ads:
                if ask(f"Disable {p} ({t})?", True):
                    plan.append((p, t))

    # 2. features
    feats = [(p, t) for p, t in FEATURES if p in enabled and not is_protected(p)]
    known |= {p for p, _ in FEATURES}
    if feats:
        say("\n== Features you might use. Say yes only if you do NOT use it ==")
        for p, t in feats:
            if ask(f"Disable '{t}'?\n   ({p})", False):
                plan.append((p, t))

    # 3. preinstalled streaming apps
    apps = [(n, p) for n, p in STREAMING if p in enabled and not is_protected(p)]
    known |= {p for _, p in STREAMING}
    if apps:
        say("\n== Streaming apps found on the TV (disabling hides them; reversible) ==")
        for n, p in apps:
            if ask(f"Disable {n}?  ({p})", False):
                plan.append((p, n))

    # 4. heuristic suspects + unidentified TCL packages
    sus = sorted(p for p in (pk["system"] & enabled)
                 if p not in known and not is_protected(p) and p not in STOCK_LAUNCHERS
                 and p != SETUPWRAITH and HEURISTIC.search(p))
    if sus:
        say("\n== Possible ads/telemetry found by name (heuristic, default keep) ==")
        for p in sus:
            if ask(f"Disable {p}?", False):
                plan.append((p, "heuristic match"))
    tcl_unknown = sorted(p for p in (pk["system"] & enabled)
                         if p.startswith("com.tcl.") and p not in known and not is_protected(p)
                         and p not in {x for x, _ in plan})
    if tcl_unknown:
        path = os.path.join(ctx.work, "unidentified-tcl-packages.txt")
        with open(path, "w") as f:
            f.write("\n".join(tcl_unknown) + "\n")
        say(f"\nLeft alone: {len(tcl_unknown)} unidentified com.tcl.* packages (listed in {path}).")
        say("Many are the TV's plumbing. Do not disable ones you cannot identify.")

    # 5. animations
    if ask("\nSpeed up the interface by halving animation durations?", True):
        for key in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
            put_setting(ctx, "global", key, "0.5")

    if not plan:
        say("Nothing to disable.")
        return
    say(f"\nApplying {len(plan)} changes in batches of 10, with a screenshot and health check after each.")
    pkgs = [p for p, _ in plan]
    for i in range(0, len(pkgs), 10):
        batch = pkgs[i:i + 10]
        if not apply_batch(ctx, batch, f"batch {i // 10 + 1}"):
            say("Stopped. Everything in the failed batch was re-enabled.")
            return
        if i + 10 < len(pkgs) and not ask(f"Batch {i // 10 + 1} OK (see report.html). Continue?", True):
            break
    say("\nDebloat finished.")


def pick_favourites(ctx):
    pk = installed(ctx.dev)
    present = pk["enabled"]
    cands = [(n, p) for n, p in KNOWN_APPS if p in present]
    seen = {p for _, p in cands}
    for p in sorted(pk["user"] & present):
        if p not in seen and p != LAUNCHER_PKG:
            cands.append((p, p))
    if not cands:
        say("No apps found for the dock; you can add them on the TV later (All apps > hold OK).")
        return []
    say("\nPick the apps for your dock (in order), e.g. 1,3,2. Enter for the first 7.")
    for i, (n, p) in enumerate(cands, 1):
        say(f"  {i:2d}) {n}" + ("" if n == p else f"  ({p})"))
    raw = ask_text("Dock apps:", "")
    if not raw:
        return [p for _, p in cands[:7]]
    out = []
    for tok in re.split(r"[,\s]+", raw):
        if tok.isdigit() and 1 <= int(tok) <= len(cands):
            out.append(cands[int(tok) - 1][1])
    return out


def verify_home(ctx, favourites=None):
    d = ctx.dev
    say("\nHome-button test from inside apps:")
    tests = [("Settings", "am start -a android.settings.SETTINGS")]
    if favourites:
        p = favourites[0]
        tests.append((p, f"monkey -p {p} -c android.intent.category.LEANBACK_LAUNCHER 1"))
    allok = True
    for label, cmd in tests:
        d.sh(cmd, 30)
        time.sleep(4)
        inside = resumed(d)
        shot(ctx, f"inside {label}")
        d.sh("input keyevent KEYCODE_HOME")
        time.sleep(2.5)
        after = resumed(d)
        shot(ctx, f"after Home from {label}")
        ok = after.startswith(LAUNCHER_PKG)
        say(f"  {label}: was in {inside or '?'}, Home went to {after or '?'} -> {'PASS' if ok else 'FAIL'}")
        allok &= ok
    return allok


def cmd_launcher(ctx):
    d = ctx.dev
    apk = ctx.args.apk
    if not apk or not os.path.exists(apk):
        for guess in ("tvdock.apk", "launcher/tvdock.apk", "launcher/app/build/outputs/apk/debug/app-debug.apk"):
            if os.path.exists(guess):
                apk = guess
                break
    if not apk or not os.path.exists(apk):
        say("\nLauncher APK not found. Build it first (see README: GitHub Actions makes tvdock.apk),")
        say("then run:  python tvtool.py launcher --apk tvdock.apk")
        return False
    ensure_backup(ctx)
    old_home = resolve_home(d)
    say(f"\nInstalling {apk} ...")
    d.install(apk)
    record(ctx, type="install", pkg=LAUNCHER_PKG)
    favs = pick_favourites(ctx)
    arg = f"--es favorites {','.join(favs)}" if favs else ""
    d.sh(f"am start -n {LAUNCHER_ACT} {arg}", 30)
    time.sleep(3)
    shot(ctx, "new home screen first launch")
    out = d.sh(f"cmd package set-home-activity {LAUNCHER_ACT}", 30)
    record(ctx, type="home", old=old_home, new=LAUNCHER_ACT)
    say("  set as default home: " + out.strip()[:100])
    pk = installed(d)
    if SETUPWRAITH in pk["enabled"] and ask(
            "Google TV's setup wizard (setupwraith) can hijack the Home button. Disable it?", True):
        disable_pkg(ctx, SETUPWRAITH)
    ok = verify_home(ctx, favs)
    if not ok:
        stock = [p for p in STOCK_LAUNCHERS if p in installed(d)["enabled"]]
        say("\nHome still opens something else. The stock launcher is winning.")
        if stock and ask("Disable the stock launcher(s) now? (reversible; the new launcher takes over)", True):
            for p in stock:
                disable_pkg(ctx, p)
            time.sleep(2)
            d.sh(f"cmd package set-home-activity {LAUNCHER_ACT}", 30)
            ok = verify_home(ctx, favs)
    elif ask("\nHome works. Also disable the stock launcher to save RAM? (reversible)", False):
        for p in [p for p in STOCK_LAUNCHERS if p in installed(d)["enabled"]]:
            disable_pkg(ctx, p)
        ok = verify_home(ctx, favs)
    healthy_ok, why = healthy(d)
    say("\nLauncher step: " + ("PASS" if ok and healthy_ok else f"needs attention ({why})"))
    return ok


def cmd_measure(ctx, label):
    m = meminfo(ctx.dev)
    say(f"  RAM {label}: {m['avail_mb']} MB available of {m['total_mb']} MB")
    return m


def summary(ctx):
    path = os.path.join(ctx.work, "state.json")
    before = json.load(open(path)).get("mem_before") if os.path.exists(path) else None
    after = meminfo(ctx.dev)
    n_dis = len([c for c in ctx.changes if c["type"] == "disable"])
    say("\n================ SUMMARY ================")
    say(f"Packages disabled: {n_dis}  (nothing uninstalled)")
    if before:
        say(f"Available RAM: {before['avail_mb']} MB -> {after['avail_mb']} MB "
            "(compare after a reboot for a fair number)")
    say(f"Report with screenshots : {ctx.report_path}")
    say(f"Restore script          : {os.path.join(ctx.work, 'restore.sh')}")
    say("Manual extra: on the TV, Settings > Privacy > Ads > Reset/Delete advertising ID.")


def cmd_restore(ctx):
    say("Restoring everything this tool changed...")
    for p in STOCK_LAUNCHERS + [SETUPWRAITH]:
        ctx.dev.sh(f"pm enable {p}")
    for c in reversed(ctx.changes):
        t = c["type"]
        if t == "disable":
            say(f"  enable {c['pkg']}: {ctx.dev.sh('pm enable ' + c['pkg']).strip()}")
        elif t == "setting":
            if c["old"] in (None, "null"):
                ctx.dev.sh(f"settings delete {c['ns']} {c['key']}")
            else:
                ctx.dev.sh(f"settings put {c['ns']} {c['key']} {c['old']}")
            say(f"  setting {c['key']} restored")
        elif t == "home" and c.get("old"):
            say("  home: " + ctx.dev.sh(f"cmd package set-home-activity {c['old']}").strip())
    if ctx.args.remove_launcher:
        say(ctx.dev.sh(f"pm uninstall {LAUNCHER_PKG}").strip())
    shot(ctx, "after restore")
    say("Restore done. Reboot the TV to finish.")


def cmd_run(ctx):
    cmd_probe(ctx)
    if not ask("\nIs this your TV and do you want to continue?", True):
        return
    cmd_backup(ctx)
    cmd_debloat(ctx)
    ok = cmd_launcher(ctx)
    if ok and ask("\nReboot the TV now to apply everything and re-test the Home button?", False):
        ctx.dev.reboot()
        if reconnect(ctx):
            shot(ctx, "after reboot")
            verify_home(ctx)
    summary(ctx)


def main(argv=None):
    global ASSUME_YES
    ap = argparse.ArgumentParser(description="Reversibly debloat an Android TV over Wi-Fi ADB.")
    ap.add_argument("command", choices=["scan", "probe", "backup", "debloat", "launcher", "verify",
                                        "restore", "run"], nargs="?", default="run")
    ap.add_argument("--ip", help="TV IP or IP:port (skips scanning)")
    ap.add_argument("--pure", action="store_true", help="force the adb-shell Python transport")
    ap.add_argument("--workdir", default="tv-data")
    ap.add_argument("--apk", help="path to tvdock.apk (the launcher)")
    ap.add_argument("-y", "--yes", action="store_true", help="accept defaults (ads group yes, features no)")
    ap.add_argument("--remove-launcher", action="store_true", help="with restore: also remove tvdock")
    args = ap.parse_args(argv)
    ASSUME_YES = args.yes
    os.makedirs(args.workdir, exist_ok=True)
    if args.command == "scan":
        found, ip = scan_network()
        say(f"phone {ip}; TVs with ADB: {found or 'none (see below)'}")
        if not found:
            say(HOWTO_ENABLE)
        return 0
    ctx = connect(args)
    handlers = {"probe": cmd_probe, "backup": cmd_backup, "debloat": cmd_debloat,
                "launcher": cmd_launcher, "restore": cmd_restore, "run": cmd_run,
                "verify": verify_home}
    try:
        handlers[args.command](ctx)
    finally:
        ctx.dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

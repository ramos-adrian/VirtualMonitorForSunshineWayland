#!/usr/bin/env python3
"""Save / switch / restore the GNOME (mutter) monitor layout through `gdctl`.

  gnome-layout.py solo    --vendor LNX --product NAME [--scale 1] [--mode WxH]
  gnome-layout.py restore
  gnome-layout.py status  --vendor LNX --product NAME

`solo` saves the current layout (only if a physical monitor is active), then
leaves ONLY the virtual monitor enabled. `restore` brings the saved layout back.
The virtual monitor is found by EDID vendor/product, so the connector name
(which mutter spells differently from the kernel) never matters.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "virtual-monitor"
SAVED = STATE / "layout.json"
TREE = re.compile(r"^[│├└─\s]+")


def gdctl(*args):
    try:
        return subprocess.run(["gdctl", *args], capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip().splitlines()
        sys.exit(f"gdctl failed ({err[-1] if err else 'no output'}).\n"
                 "This needs a running GNOME session: log in graphically (not a tty) and retry.")


def parse(text):
    """Parse `gdctl show` (optionally with --modes) into monitors + logical monitors."""
    mons, logical = {}, []
    section = cur = lcur = None
    want_mode = in_members = False
    for raw in text.splitlines():
        line = TREE.sub("", raw).strip()
        if raw.startswith("Monitors:"):
            section = "m"
            continue
        if raw.startswith("Logical monitors:"):
            section = "l"
            continue
        if section == "m":
            if m := re.match(r"Monitor (\S+) \((.*)\)", line):
                cur = mons[m[1]] = dict(name=m[1], desc=m[2], vendor="", product="", mode=None, modes=[])
            elif cur is None:
                continue
            elif line.startswith("Vendor:"):
                cur["vendor"] = line.split(":", 1)[1].strip()
            elif line.startswith("Product:"):
                cur["product"] = line.split(":", 1)[1].strip()
            elif line.startswith("Current mode"):
                want_mode = True
            elif want_mode:
                cur["mode"], want_mode = line, False
            elif re.fullmatch(r"\d+x\d+@[\d.]+", line):
                cur["modes"].append(line)
        elif section == "l":
            if line.startswith("Logical monitor"):
                lcur = dict(x=0, y=0, scale="1", transform="normal", primary=False, monitors=[])
                logical.append(lcur)
                in_members = False
            elif lcur is None:
                continue
            elif m := re.match(r"Position: \((-?\d+), (-?\d+)\)", line):
                lcur["x"], lcur["y"] = int(m[1]), int(m[2])
            elif line.startswith("Scale:"):
                lcur["scale"] = line.split(":", 1)[1].strip()
            elif line.startswith("Transform:"):
                lcur["transform"] = line.split(":", 1)[1].strip()
            elif line.startswith("Primary:"):
                lcur["primary"] = line.endswith("yes")
            elif line.startswith("Monitors:"):
                in_members = True
            elif in_members and (m := re.match(r"(\S+) \(", line)):
                lcur["monitors"].append(m[1])
    return mons, logical


def snapshot():
    mons, logical = parse(gdctl("show"))
    modes, _ = parse(gdctl("show", "--modes"))
    for n, m in mons.items():
        m["modes"] = modes.get(n, {}).get("modes", [])
    return mons, logical


def find_virtual(mons, vendor, product):
    """Match by product name (always ours); vendor only breaks ties, so a changed VENDOR still works pre-reboot."""
    hits = [m for m in mons.values() if m["product"] == product]
    return next((m for m in hits if m["vendor"] == vendor), hits[0] if hits else None)


def apply(args, persistent=False):
    args = (["-P"] if persistent else []) + args
    print("gdctl set", " ".join(args))
    subprocess.run(["gdctl", "set", *args], check=True)


def build_args(layout):
    """layout: list of logical monitors with monitors=[{name, mode}] -> gdctl arguments."""
    ox, oy = min(lm["x"] for lm in layout), min(lm["y"] for lm in layout)  # drop gaps
    args = []
    for lm in layout:
        args += ["-L"] + (["-p"] if lm["primary"] else []) + [
            "-x", str(lm["x"] - ox), "-y", str(lm["y"] - oy), "-s", lm["scale"], "-t", lm["transform"]]
        for m in lm["monitors"]:
            args += ["-M", m["name"]] + (["--mode", m["mode"]] if m["mode"] else [])
    return args


def cmd_solo(a):
    mons, logical = snapshot()
    virt = find_virtual(mons, a.vendor, a.product)
    if not virt:
        sys.exit(f"virtual monitor ({a.vendor}/{a.product}) not visible to GNOME. "
                 "Did you reboot after install? Run: virtual-monitor.sh status")
    enabled = {n for lm in logical for n in lm["monitors"]}
    if enabled - {virt["name"]}:  # a physical monitor is active -> remember this layout
        STATE.mkdir(parents=True, exist_ok=True)
        saved = []
        for lm in logical:
            if virt["name"] in lm["monitors"]:
                continue
            saved.append({**lm, "monitors": [dict(name=n, mode=mons[n]["mode"]) for n in lm["monitors"]]})
        SAVED.write_text(json.dumps(saved, indent=1))
    want = a.mode or ""
    mode = next((m for m in virt["modes"] if want and m.startswith(want + "@")), None) \
        or virt["mode"] or (virt["modes"][0] if virt["modes"] else None)
    args = ["-L", "-p", "-x", "0", "-y", "0", "-s", str(a.scale), "-M", virt["name"]]
    if mode:
        args += ["--mode", mode]
    apply(args)


def cmd_restore(_):
    if not SAVED.exists():
        sys.exit("no saved layout; nothing to restore")
    apply(build_args(json.loads(SAVED.read_text())))


def cmd_physical(a):
    """Persist the current layout WITHOUT the virtual monitor (also what GDM's login screen will use)."""
    mons, logical = snapshot()
    virt = find_virtual(mons, a.vendor, a.product)
    layout = [{**lm, "monitors": [dict(name=n, mode=mons[n]["mode"]) for n in lm["monitors"]]}
              for lm in logical if not (virt and virt["name"] in lm["monitors"])]
    if not layout:
        sys.exit("no physical monitor is active (streaming right now?). End the Moonlight session or run './virtual-monitor.sh off', then retry")
    apply(build_args(layout), persistent=True)


def cmd_status(a):
    mons, logical = snapshot()
    virt = find_virtual(mons, a.vendor, a.product)
    print("monitors seen by GNOME:", ", ".join(mons) or "-")
    print("virtual monitor:", virt["name"] if virt else "NOT FOUND",
          f"(mode {virt['mode']})" if virt and virt["mode"] else "")
    print("enabled:", ", ".join(n for lm in logical for n in lm["monitors"]) or "-")
    print("saved layout:", SAVED if SAVED.exists() else "none")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("solo", cmd_solo), ("restore", cmd_restore), ("physical", cmd_physical), ("status", cmd_status)):
        s = sub.add_parser(name)
        s.add_argument("--vendor", default="LNX")
        s.add_argument("--product", default="VirtualMon")
        s.add_argument("--scale", default="1")
        s.add_argument("--mode", default="")
        s.set_defaults(fn=fn)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

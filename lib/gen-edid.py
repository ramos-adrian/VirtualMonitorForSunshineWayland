#!/usr/bin/env python3
"""Generate a 128-byte EDID 1.4 blob for a virtual monitor (no dependencies).

Timings use CVT Reduced Blanking v1, the same family `cvt -r` produces.
The first mode is the preferred (native) one; an optional second mode is
advertised too so GNOME lists it as a selectable resolution.

Usage:
  gen-edid.py -o out.bin -W 2000 -H 1200 -r 60 [-d 10.4] [-n NAME] [-x 1920x1080@60]
  gen-edid.py --decode out.bin
"""
import argparse
import math
import re
import sys


def cvt_rb(w, h, hz):
    """CVT reduced-blanking v1 timings -> dict (pixel clock in kHz)."""
    h_blank, h_fp, h_sync = 160, 48, 32
    # Same integer aspect-ratio rules as the reference `cvt` tool (X.org cvt.c).
    if h % 3 == 0 and h * 4 // 3 == w:
        vsync = 4
    elif h % 9 == 0 and h * 16 // 9 == w:
        vsync = 5
    elif h % 10 == 0 and h * 16 // 10 == w:
        vsync = 6
    elif (h % 4 == 0 and h * 5 // 4 == w) or (h % 9 == 0 and h * 15 // 9 == w):
        vsync = 7
    else:
        vsync = 10
    v_fp, min_vbi_time_us = 3, 460.0
    h_period = (1e6 / hz - min_vbi_time_us) / h
    vbi = max(math.floor(min_vbi_time_us / h_period) + 1, v_fp + vsync + 6)
    h_total, v_total = w + h_blank, h + vbi
    clock_khz = round(h_total * v_total * hz / 10000) * 10  # EDID resolution: 10 kHz
    return dict(w=w, h=h, hz=hz, h_blank=h_blank, h_fp=h_fp, h_sync=h_sync,
                v_blank=vbi, v_fp=v_fp, v_sync=vsync, clock_khz=clock_khz,
                h_total=h_total, v_total=v_total)


def dtd(t, w_mm, h_mm):
    """18-byte detailed timing descriptor."""
    c = t["clock_khz"] // 10
    assert c <= 0xFFFF, "pixel clock too high for a DTD"
    for k, lim in (("w", 4095), ("h", 4095), ("h_blank", 4095), ("v_blank", 4095)):
        assert t[k] <= lim, f"{k} out of range"
    d = bytearray(18)
    d[0], d[1] = c & 0xFF, c >> 8
    d[2], d[3] = t["w"] & 0xFF, t["h_blank"] & 0xFF
    d[4] = ((t["w"] >> 8) << 4) | (t["h_blank"] >> 8)
    d[5], d[6] = t["h"] & 0xFF, t["v_blank"] & 0xFF
    d[7] = ((t["h"] >> 8) << 4) | (t["v_blank"] >> 8)
    d[8], d[9] = t["h_fp"] & 0xFF, t["h_sync"] & 0xFF
    d[10] = ((t["v_fp"] & 0xF) << 4) | (t["v_sync"] & 0xF)
    d[11] = ((t["h_fp"] >> 8) << 6) | ((t["h_sync"] >> 8) << 4) | \
            ((t["v_fp"] >> 4) << 2) | (t["v_sync"] >> 4)
    d[12], d[13] = w_mm & 0xFF, h_mm & 0xFF
    d[14] = ((w_mm >> 8) << 4) | (h_mm >> 8)
    d[15] = d[16] = 0
    d[17] = 0x1A  # digital separate sync, +hsync -vsync (CVT-RB)
    return bytes(d)


def text_desc(tag, text):
    b = text.encode("ascii")[:13]
    if len(b) < 13:
        b += b"\n" + b" " * (12 - len(b))
    return bytes([0, 0, 0, tag, 0]) + b


def range_desc(vmin, vmax, hmin, hmax, pclk_mhz):
    return bytes([0, 0, 0, 0xFD, 0, vmin, vmax, hmin, hmax,
                  math.ceil(pclk_mhz / 10), 0x00, 0x0A, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20])


def build(modes, diag_in, name, mfg="LNX"):
    main = modes[0]
    ar = main["w"] / main["h"]
    h_mm = diag_in * 25.4 / math.sqrt(1 + ar * ar)
    w_mm, h_mm = round(h_mm * ar), round(h_mm)

    e = bytearray(128)
    e[0:8] = bytes([0, 255, 255, 255, 255, 255, 255, 0])
    mid = ((ord(mfg[0]) - 64) << 10) | ((ord(mfg[1]) - 64) << 5) | (ord(mfg[2]) - 64)
    e[8], e[9] = mid >> 8, mid & 0xFF
    e[10], e[11] = 0x01, 0x00           # product code
    e[12:16] = (1).to_bytes(4, "little")  # serial
    e[16], e[17] = 1, 34                # week 1, year 2024
    e[18], e[19] = 1, 4                 # EDID 1.4
    e[20] = 0xA5                        # digital, 8 bpc, DisplayPort
    e[21], e[22] = round(w_mm / 10), round(h_mm / 10)  # size in cm
    e[23] = 120                         # gamma 2.2
    e[24] = 0x06                        # RGB 4:4:4, sRGB, preferred timing = first DTD
    e[25:35] = bytes([0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54])  # sRGB primaries
    e[35:38] = b"\x00\x00\x00"
    std = [b"\xD1\xC0"] + [b"\x01\x01"] * 7  # 1920x1080@60 (16:9) then unused
    e[38:54] = b"".join(std)

    desc = [dtd(main, w_mm, h_mm)]
    desc.append(dtd(modes[1], w_mm, h_mm) if len(modes) > 1 else bytes([0, 0, 0, 0x10, 0] + [0] * 13))
    hf_max = max(m["clock_khz"] / m["h_total"] for m in modes)
    vmax = max(m["hz"] for m in modes)
    pclk = max(m["clock_khz"] for m in modes) / 1000
    desc.append(range_desc(24, min(255, math.ceil(vmax) + 15), 30, min(255, math.ceil(hf_max) + 5), pclk))
    desc.append(text_desc(0xFC, name))
    e[54:126] = b"".join(desc)
    e[126] = 0
    e[127] = (-sum(e[:127])) & 0xFF
    return bytes(e)


def decode(path):
    e = open(path, "rb").read()
    ok = len(e) == 128 and sum(e) % 256 == 0 and e[:8] == bytes([0, 255, 255, 255, 255, 255, 255, 0])
    print(f"{path}: {len(e)} bytes, header+checksum {'OK' if ok else 'BAD'}")
    for off in (54, 72, 90, 108):
        d = e[off:off + 18]
        if d[0] or d[1]:
            clk = (d[0] | d[1] << 8) * 10
            w = d[2] | (d[4] >> 4) << 8
            hb = d[3] | (d[4] & 15) << 8
            h = d[5] | (d[7] >> 4) << 8
            vb = d[6] | (d[7] & 15) << 8
            tot = (w + hb) * (h + vb)
            print(f"  DTD: {w}x{h} @ {clk * 1000 / tot:.3f} Hz, clock {clk / 1000:.2f} MHz, "
                  f"{(d[12] | (d[14] >> 4) << 8)}x{(d[13] | (d[14] & 15) << 8)} mm")
        elif d[3] == 0xFC:
            print("  Name:", d[5:18].decode("ascii").strip())
        elif d[3] == 0xFD:
            print(f"  Range: V {d[5]}-{d[6]} Hz, H {d[7]}-{d[8]} kHz, max clock {d[9] * 10} MHz")
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--output")
    p.add_argument("-W", "--width", type=int)
    p.add_argument("-H", "--height", type=int)
    p.add_argument("-r", "--refresh", type=float, default=60)
    p.add_argument("-d", "--diagonal", type=float, default=10.4, help="inches (physical size hint)")
    p.add_argument("-n", "--name", default="VirtualMonitor")
    p.add_argument("-v", "--vendor", default="VMN", help="3 uppercase letters (PNP id shown by GNOME)")
    p.add_argument("-x", "--extra", default="", help="optional 2nd mode WxH@R")
    p.add_argument("--decode")
    a = p.parse_args()
    if a.decode:
        sys.exit(decode(a.decode))
    if not (a.output and a.width and a.height):
        p.error("-o, -W and -H are required")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,13}", a.name):
        p.error("--name: 1-13 chars, letters/digits/_/- only")
    if not re.fullmatch(r"[A-Z]{3}", a.vendor):
        p.error("--vendor: exactly 3 uppercase letters")
    modes = [cvt_rb(a.width, a.height, a.refresh)]
    if a.extra:
        m = re.fullmatch(r"(\d+)x(\d+)(?:@([\d.]+))?", a.extra)
        if not m:
            p.error("--extra must look like 1920x1080@60")
        modes.append(cvt_rb(int(m[1]), int(m[2]), float(m[3] or 60)))
    with open(a.output, "wb") as f:
        f.write(build(modes, a.diagonal, a.name, a.vendor))
    reg = pnp_name(a.vendor)
    print(f"  GNOME will show the maker as: {reg or a.vendor + ' (not in PNP table: raw code)'}")
    sys.exit(decode(a.output))


def pnp_name(code):
    for path in ("/usr/share/hwdata/pnp.ids", "/usr/share/misc/pnp.ids"):
        try:
            for line in open(path, encoding="latin-1"):
                if line.startswith(code + "\t"):
                    return line.split("\t", 1)[1].strip()
        except OSError:
            pass
    return None


if __name__ == "__main__":
    main()

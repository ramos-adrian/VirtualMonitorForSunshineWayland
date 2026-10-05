import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

LIB = pathlib.Path(__file__).resolve().parent.parent / "lib"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, LIB / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


edid = load("gen_edid", "gen-edid.py")


class CvtTests(unittest.TestCase):
    def test_matches_cvt_reduced_blanking_1080p(self):
        # `cvt -r 1920 1080 60` -> 138.50 MHz, 2080 x 1111 total
        t = edid.cvt_rb(1920, 1080, 60)
        self.assertEqual((t["h_total"], t["v_total"]), (2080, 1111))
        self.assertAlmostEqual(t["clock_khz"] / 1000, 138.5, delta=0.2)

    def test_matches_cvt_reduced_blanking_2000x1200(self):
        t = edid.cvt_rb(2000, 1200, 60)
        self.assertEqual((t["h_total"], t["v_total"], t["v_sync"]), (2160, 1235, 10))

    def test_refresh_rate_is_accurate(self):
        t = edid.cvt_rb(2388, 1668, 60)
        hz = t["clock_khz"] * 1000 / (t["h_total"] * t["v_total"])
        self.assertAlmostEqual(hz, 60, delta=0.05)


class BuildTests(unittest.TestCase):
    def build(self, **kw):
        modes = [edid.cvt_rb(2000, 1200, 60)]
        if kw.pop("extra", False):
            modes.append(edid.cvt_rb(1920, 1080, 60))
        return edid.build(modes, 10.4, kw.get("name", "VirtualMon"), kw.get("vendor", "VMN"))

    def test_size_header_checksum(self):
        e = self.build()
        self.assertEqual(len(e), 128)
        self.assertEqual(e[:8], bytes([0, 255, 255, 255, 255, 255, 255, 0]))
        self.assertEqual(sum(e) % 256, 0)

    def test_vendor_id_encoding(self):
        e = self.build(vendor="LNX")
        mid = (e[8] << 8) | e[9]
        letters = "".join(chr(((mid >> s) & 31) + 64) for s in (10, 5, 0))
        self.assertEqual(letters, "LNX")

    def test_preferred_timing_roundtrip(self):
        e = self.build(extra=True)
        with tempfile.NamedTemporaryFile(suffix=".bin") as f:
            f.write(e); f.flush()
            out = subprocess.run([sys.executable, str(LIB / "gen-edid.py"), "--decode", f.name],
                                 capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("2000x1200 @ 60.0", out.stdout)
        self.assertIn("1920x1080 @ 59.9", out.stdout)
        self.assertIn("Name: VirtualMon", out.stdout)

    def test_cli_rejects_bad_name_and_vendor(self):
        base = [sys.executable, str(LIB / "gen-edid.py"), "-o", "/dev/null", "-W", "1280", "-H", "800"]
        for bad in (["-n", "TooLongName_123456"], ["-n", "has space"], ["-v", "ab"], ["-v", "ABCD"]):
            r = subprocess.run(base + bad, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0, bad)


layout = load("gnome_layout", "gnome-layout.py")

GDCTL_SHOW = """Monitors:
├──Monitor DP-1 (ACME 22")
│  ├──Vendor: ACM
│  ├──Product: LED MONITOR
│  ├──Serial: 000000010000
│  ├──Current mode
│  │   └──1920x1080@60.000
│  └──Preferences
│      └──Backlight: None
├──Monitor DP-4 (VMN 11")
│  ├──Vendor: VMN
│  ├──Product: VirtualMon
│  ├──Serial: 0x00000001
│  ├──Current mode
│  │   └──2000x1200@60.001
│  └──Preferences
│      └──Backlight: None
└──Monitor HDMI-2 (Other 19")
   ├──Vendor: OTH
   ├──Product: OTHER 193
   ├──Serial: 0
   └──Preferences
       └──Backlight: None

Logical monitors:
├──Logical monitor #1
│  ├──Position: (1200, 0)
│  ├──Scale: 1.0
│  ├──Transform: normal
│  ├──Primary: yes
│  └──Monitors: (1)
│      └──DP-1 (ACME 22")
└──Logical monitor #2
   ├──Position: (0, 0)
   ├──Scale: 1.6666666269302368
   ├──Transform: normal
   ├──Primary: no
   └──Monitors: (1)
       └──DP-4 (VMN 11")
"""


class LayoutTests(unittest.TestCase):
    def test_parse_monitors_and_logical(self):
        mons, logical = layout.parse(GDCTL_SHOW)
        self.assertEqual(set(mons), {"DP-1", "DP-4", "HDMI-2"})
        self.assertEqual(mons["DP-4"]["mode"], "2000x1200@60.001")
        self.assertIsNone(mons["HDMI-2"]["mode"])  # disabled monitor
        self.assertEqual(len(logical), 2)
        self.assertTrue(logical[0]["primary"])
        self.assertEqual(logical[1]["monitors"], ["DP-4"])

    def test_find_virtual_by_product_even_if_vendor_changed(self):
        mons, _ = layout.parse(GDCTL_SHOW)
        self.assertEqual(layout.find_virtual(mons, "XXX", "VirtualMon")["name"], "DP-4")
        self.assertIsNone(layout.find_virtual(mons, "VMN", "Nope"))

    def test_build_args_drops_gap_and_keeps_primary(self):
        args = layout.build_args([{"x": 1200, "y": 0, "scale": "1.0", "transform": "normal", "primary": True,
                                   "monitors": [{"name": "DP-1", "mode": "1920x1080@60.000"}]}])
        self.assertEqual(args, ["-L", "-p", "-x", "0", "-y", "0", "-s", "1.0", "-t", "normal",
                                "-M", "DP-1", "--mode", "1920x1080@60.000"])


if __name__ == "__main__":
    unittest.main()

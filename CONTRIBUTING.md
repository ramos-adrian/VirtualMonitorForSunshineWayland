# Contributing

Reports from other setups are the most valuable contribution: this was developed and tested on a single
machine (see the compatibility table in the README). Please open an issue with your distro, desktop,
GPU(s)/drivers, bootloader and the output of `./virtual-monitor.sh connectors` and `status`.

## Development

```bash
python3 -m unittest discover -s tests -v      # unit tests (EDID generator, gdctl parser)
shellcheck -x virtual-monitor.sh              # lint
VM_CONFIG=profiles/fullhd-60.conf ./virtual-monitor.sh gen   # build an EDID without root
```

Layout: `virtual-monitor.sh` (CLI), `lib/gen-edid.py` (EDID builder, no dependencies),
`lib/gnome-layout.py` (the only desktop-specific part; mutter via `gdctl`), `profiles/` (device presets).

## Adding support for another desktop (KDE, Sway/wlroots, X11...)

Only `on` / `off` / `status` / `physical` need a backend. Add `lib/<desktop>-layout.py` with the same
subcommands (`solo`, `restore`, `physical`, `status`), find the virtual monitor by its EDID product name
(`NAME`) rather than the connector name, and select the backend in `layout()` inside `virtual-monitor.sh`.
Note in the README which setup you tested it on.

## Style
Keep shell portable bash, no external dependencies besides coreutils/python3/sudo. Anything that changes the
system must be reversible by `uninstall` and must only create new files where possible.

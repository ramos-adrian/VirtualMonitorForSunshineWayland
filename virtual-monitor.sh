#!/usr/bin/env bash
# virtual-monitor.sh - create a virtual monitor (forced DRM connector + fake EDID) for
# headless-style streaming with Sunshine/Moonlight on Linux. Run without arguments for help.
# Docs: README.md
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
CONF="${VM_CONFIG:-$ROOT/config/virtual-monitor.conf}"
BUILD="$ROOT/build"
EDID_FILE="virtual-monitor.bin"
FW_DIR=/usr/lib/firmware/edid
GRUB_DROPIN=/etc/default/grub.d/99-virtual-monitor.cfg
GRUB_MARK_BEGIN="# BEGIN virtual-monitor"
GRUB_MARK_END="# END virtual-monitor"
INITRAMFS_HOOK=/etc/initramfs-tools/hooks/virtual-monitor-edid
DRACUT_CONF=/etc/dracut.conf.d/99-virtual-monitor.conf
MKINITCPIO_CONF=/etc/mkinitcpio.conf.d/virtual-monitor.conf

die()  { echo "error: $*" >&2; exit 1; }
warn() { echo "warning: $*" >&2; }

# First run: create the (git-ignored) active config from a stock profile.
if [ ! -f "$CONF" ]; then
  [ -z "${VM_CONFIG:-}" ] || die "config file not found: $CONF"
  mkdir -p "$(dirname "$CONF")" && cp "$ROOT/config/virtual-monitor.conf.example" "$CONF"
  echo "Created $CONF from config/virtual-monitor.conf.example (edit it, or pick a preset: $0 profile <name>)" >&2
fi
# shellcheck disable=SC1090
source "$CONF"
: "${CONNECTOR:=auto}" "${WIDTH:?WIDTH missing in $CONF}" "${HEIGHT:?HEIGHT missing}" "${REFRESH:=60}"
: "${EXTRA_MODE:=}" "${DIAGONAL_INCH:=10}" "${NAME:=VirtualMon}" "${VENDOR:=VMN}" "${SCALE:=1}"

need_root() { [ "$EUID" -eq 0 ] || exec sudo VM_CONFIG="$CONF" "$0" "$@"; }

# ---------------------------------------------------------------- connectors
card_of()   { local p; for p in /sys/class/drm/card*-"$1"; do [ -e "$p" ] && { basename "$p" | sed "s/-$1\$//"; return 0; }; done; return 1; }
driver_of() { basename "$(readlink -f "/sys/class/drm/$1/device/driver" 2>/dev/null)" 2>/dev/null || echo unknown; }

# Candidate = unplugged connector on a driver that honours EDID overrides (not nvidia).
# Preference: DP, then HDMI, then the rest. Internal/virtual outputs are skipped.
auto_connector() {
  local p name card rank best=""
  for rank in DP- HDMI-A- ""; do
    for p in /sys/class/drm/card*-*; do
      name=${p##*/}; card=${name%%-*}; name=${name#*-}
      case "$name" in eDP-*|LVDS-*|Virtual-*|Writeback-*) continue ;; esac
      [ "$(cat "$p/status")" = disconnected ] || continue
      case "$(driver_of "$card")" in nvidia|unknown) continue ;; esac
      case "$name" in "$rank"*) best=$name; break 2 ;; esac
    done
  done
  [ -n "$best" ] && echo "$best"
  return 0
}

# Resolve CONNECTOR: explicit name > connector we already forced on the kernel cmdline > auto-detect.
resolve_connector() {
  if [ "$CONNECTOR" != auto ]; then echo "$CONNECTOR"; return; fi
  local forced found
  forced=$(grep -o 'drm.edid_firmware=[^: ]*:edid/virtual-monitor.bin' /proc/cmdline 2>/dev/null | head -1 | sed 's/drm.edid_firmware=//;s/:.*//' || true)
  if [ -n "$forced" ]; then echo "$forced"; return; fi
  found=$(auto_connector)
  [ -n "$found" ] || die "no suitable free connector found (CONNECTOR=auto). See: $0 connectors. NVIDIA-only systems need a dummy plug."
  echo "$found"
}

cmd_connectors() {
  echo "DRM connectors (candidates: 'disconnected' and not on the nvidia driver):"
  local p card
  for p in /sys/class/drm/card*-*; do
    card=$(basename "$p"); card=${card%%-*}
    printf "  %-18s %-13s driver=%s\n" "${p##*/}" "$(cat "$p/status")" "$(driver_of "$card")"
  done
  echo; echo "Resolved CONNECTOR ($CONNECTOR): $(resolve_connector 2>/dev/null || echo none)"
}

# ---------------------------------------------------------------- EDID
cmd_gen() {
  mkdir -p "$BUILD"
  python3 "$ROOT/lib/gen-edid.py" -o "$BUILD/$EDID_FILE" -W "$WIDTH" -H "$HEIGHT" -r "$REFRESH" \
    -d "$DIAGONAL_INCH" -n "$NAME" -v "$VENDOR" ${EXTRA_MODE:+-x "$EXTRA_MODE"}
}

kargs() { local c; c=$(resolve_connector); echo "video=${c}:e drm.edid_firmware=${c}:edid/${EDID_FILE}"; }
cmd_kargs() { kargs; }

# ---------------------------------------------------------------- install / uninstall
regen_initrd() {
  if   command -v update-initramfs >/dev/null; then update-initramfs -u
  elif command -v dracut           >/dev/null; then dracut -f
  elif command -v mkinitcpio       >/dev/null; then mkinitcpio -P
  else warn "no initrd generator found; make sure $FW_DIR/$EDID_FILE ends up in your initrd"; fi
}

regen_grub() {
  if   command -v update-grub    >/dev/null; then update-grub
  elif command -v grub-mkconfig  >/dev/null; then grub-mkconfig -o /boot/grub/grub.cfg
  elif command -v grub2-mkconfig >/dev/null; then
    grub2-mkconfig -o "$([ -d /boot/grub2 ] && echo /boot/grub2/grub.cfg || echo /boot/grub/grub.cfg)"
  fi
}

install_bootloader() {
  local args="$1" mk
  mk=$(command -v grub-mkconfig || command -v grub2-mkconfig || true)
  if [ -z "$mk" ]; then
    warn "GRUB not found. Add these kernel parameters in your bootloader yourself:"
    echo "    $args"; return
  fi
  if grep -q 'default/grub.d' "$mk" 2>/dev/null; then   # Debian/Ubuntu: drop-in directory is sourced
    mkdir -p "$(dirname "$GRUB_DROPIN")"
    # shellcheck disable=SC2016  # the ${...} must stay literal: GRUB expands it
    printf '# Installed by virtual-monitor.sh\nGRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} %s"\n' "$args" > "$GRUB_DROPIN"
  else                                                   # Fedora/Arch/...: marked block in /etc/default/grub
    [ -f /etc/default/grub.vm-backup ] || cp -p /etc/default/grub /etc/default/grub.vm-backup
    sed -i "/$GRUB_MARK_BEGIN/,/$GRUB_MARK_END/d" /etc/default/grub
    # shellcheck disable=SC2016
    printf '%s\nGRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} %s"\n%s\n' "$GRUB_MARK_BEGIN" "$args" "$GRUB_MARK_END" >> /etc/default/grub
  fi
  regen_grub
}

cmd_install() {
  need_root install
  local conn card; conn=$(resolve_connector); card=$(card_of "$conn") || die "connector $conn does not exist (see: $0 connectors)"
  if [ "$(cat "/sys/class/drm/$card-$conn/status")" = connected ] && ! grep -q "edid/$EDID_FILE" /proc/cmdline; then
    die "$conn is a real, connected monitor"
  fi
  [ "$(driver_of "$card")" != nvidia ] || warn "$conn is on the nvidia driver, which ignores drm.edid_firmware; this will not work. Use a dummy plug."
  case "$conn" in DVI*) warn "$conn is single-link DVI (max ~165 MHz pixel clock); high resolutions may not fit. Prefer a DP/HDMI connector." ;; esac
  cmd_gen
  install -Dm644 "$BUILD/$EDID_FILE" "$FW_DIR/$EDID_FILE"

  # The GPU driver usually loads from the initrd, so the EDID must be inside it. Cover each generator present.
  if [ -d /etc/initramfs-tools ]; then
    cat > "$INITRAMFS_HOOK" <<EOF
#!/bin/sh
# Installed by virtual-monitor.sh - copies the fake EDID into the initramfs.
PREREQ=""
prereqs() { echo "\$PREREQ"; }
case "\$1" in prereqs) prereqs; exit 0 ;; esac
. /usr/share/initramfs-tools/hook-functions
mkdir -p "\${DESTDIR}$FW_DIR"
cp -p "$FW_DIR/$EDID_FILE" "\${DESTDIR}$FW_DIR/"
EOF
    chmod 755 "$INITRAMFS_HOOK"
  fi
  if command -v dracut >/dev/null; then
    mkdir -p "$(dirname "$DRACUT_CONF")"; echo "install_items+=\" $FW_DIR/$EDID_FILE \"" > "$DRACUT_CONF"
  fi
  if command -v mkinitcpio >/dev/null; then
    mkdir -p "$(dirname "$MKINITCPIO_CONF")"; echo "FILES+=($FW_DIR/$EDID_FILE)" > "$MKINITCPIO_CONF"
  fi

  install_bootloader "$(kargs)"
  regen_initrd
  echo; echo "Installed for connector $conn. REBOOT to activate (or try: sudo $0 reload)."
  echo "Check the initrd contains it:  lsinitrd /boot/<initrd> | grep edid   (dracut)   or   lsinitramfs /boot/<initrd> | grep edid"
}

cmd_uninstall() {
  need_root uninstall
  rm -fv "$GRUB_DROPIN" "$INITRAMFS_HOOK" "$DRACUT_CONF" "$MKINITCPIO_CONF" "$FW_DIR/$EDID_FILE"
  if [ -f /etc/default/grub ] && grep -q "$GRUB_MARK_BEGIN" /etc/default/grub; then
    sed -i "/$GRUB_MARK_BEGIN/,/$GRUB_MARK_END/d" /etc/default/grub
  fi
  regen_grub; regen_initrd
  echo "Removed. Reboot to drop the virtual connector."
}

cmd_reload() {  # live EDID swap through debugfs, no reboot (best effort)
  need_root reload
  cmd_gen
  local conn card dbg; conn=$(resolve_connector); card=$(card_of "$conn") || die "no connector $conn"
  dbg=/sys/kernel/debug/dri/${card#card}/$conn
  [ -e "$dbg/edid_override" ] || die "$dbg/edid_override not available (needs debugfs and root). Use install + reboot."
  cat "$BUILD/$EDID_FILE" > "$dbg/edid_override"
  echo detect > "/sys/class/drm/$card-$conn/status"
  echo "EDID override applied to $conn; check with: $0 status"
}

# ---------------------------------------------------------------- desktop layout (GNOME / mutter)
layout() {
  command -v gdctl >/dev/null || die "gdctl not found. on/off/login support GNOME (mutter) only; see README 'Other desktops'."
  python3 "$ROOT/lib/gnome-layout.py" "$1" --vendor "$VENDOR" --product "$NAME" --scale "$SCALE" --mode "${WIDTH}x${HEIGHT}"
}
cmd_on()  { layout solo; }      # only the virtual monitor stays active
cmd_off() { layout restore; }   # bring physical monitors back

cmd_status() {
  local conn card; conn=$(resolve_connector 2>/dev/null || echo "?")
  echo "config:    $CONF  (${WIDTH}x${HEIGHT}@${REFRESH}, connector $conn)"
  echo "cmdline:   $(grep -o "video=[^ ]*:e\|drm.edid_firmware=[^ ]*" /proc/cmdline | tr '\n' ' ')"
  if card=$(card_of "$conn" 2>/dev/null); then echo "connector: $card-$conn is $(cat "/sys/class/drm/$card-$conn/status")"; fi
  layout status
}

# Make the display manager's login screen use the same layout, minus the virtual monitor.
cmd_login() {
  [ "$EUID" -ne 0 ] || die "run this as your normal user (it calls sudo itself for the display-manager part)"
  local gdm_user gdm_home u
  for u in gdm Debian-gdm; do getent passwd "$u" >/dev/null && { gdm_user=$u; break; }; done
  [ -n "${gdm_user:-}" ] || die "GDM not found; this command only handles GDM (other display managers: see README)"
  layout physical
  gdm_home=$(getent passwd "$gdm_user" | cut -d: -f6)
  sudo install -D -o "$gdm_user" -g "$gdm_user" -m 644 "${XDG_CONFIG_HOME:-$HOME/.config}/monitors.xml" "$gdm_home/.config/monitors.xml"
  echo "GDM login screen will use the same layout (virtual monitor disabled): $gdm_home/.config/monitors.xml"
}

cmd_profile() {
  local f="$ROOT/profiles/${1:-}.conf"
  [ -f "$f" ] || { echo "profiles:"; for p in "$ROOT"/profiles/*.conf; do p=${p##*/}; echo "  ${p%.conf}"; done; exit 1; }
  mkdir -p "$ROOT/config"; cp "$f" "$ROOT/config/virtual-monitor.conf"
  echo "Active profile: $1. Now run: sudo $0 install (then reboot) or sudo $0 reload"
}

# ---------------------------------------------------------------- Sunshine
sunshine_dir() {
  local d
  for d in "${XDG_CONFIG_HOME:-$HOME/.config}/sunshine" "$HOME/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine"; do
    [ -f "$d/apps.json" ] && { echo "$d"; return; }
  done
  die "Sunshine config (apps.json) not found. Start Sunshine once so it creates it, then retry."
}
sunshine_unit() { systemctl --user list-unit-files 2>/dev/null | awk '/[Ss]unshine.*\.service/ {print $1; exit}' || true; }
sunshine_restart_hint() { local u; u=$(sunshine_unit); if [ -n "$u" ]; then echo "systemctl --user restart $u"; else echo "restart the sunshine process"; fi; }

cmd_sunshine() {  # add the app entry + KMS/VAAPI settings to Sunshine (originals backed up once)
  local conn card render dir r
  conn=$(resolve_connector); card=$(card_of "$conn") || die "no connector $conn"
  render=""; for r in "/sys/class/drm/$card/device/drm"/renderD*; do [ -e "$r" ] && { render=${r##*/}; break; }; done
  [ -n "$render" ] || die "no render node for $card"
  dir=$(sunshine_dir)
  python3 - "$ROOT/virtual-monitor.sh" "/dev/dri/$render" "$dir" <<'EOF'
import json, os, shutil, sys
script, render, d = sys.argv[1:]
apps, conf = f"{d}/apps.json", f"{d}/sunshine.conf"
if not os.path.exists(apps + ".orig"):
    shutil.copy(apps, apps + ".orig")  # pristine copy, made once
data = json.load(open(apps))
data["apps"] = [a for a in data["apps"] if a.get("name") != "Virtual Monitor"]
data["apps"].append({"name": "Virtual Monitor", "image-path": "desktop.png",
                     "prep-cmd": [{"do": f"{script} on", "undo": f"{script} off"}]})
json.dump(data, open(apps, "w"), indent=2)
want = {"capture": "kms", "encoder": "vaapi", "adapter_name": render}
if os.path.exists(conf) and not os.path.exists(conf + ".orig"):
    shutil.copy(conf, conf + ".orig")
lines = [l for l in (open(conf).read().splitlines() if os.path.exists(conf) else [])
         if l.split("=")[0].strip() not in want]
lines += [f"{k} = {v}" for k, v in want.items()]
open(conf, "w").write("\n".join(lines) + "\n")
print(f"Sunshine ({d}): app 'Virtual Monitor' added; capture=kms encoder=vaapi adapter_name={render}")
EOF
  echo "Restart Sunshine to apply: $(sunshine_restart_hint)"
}

cmd_sunshine_restore() {
  local d f; d=$(sunshine_dir)
  for f in apps.json sunshine.conf; do
    if [ -f "$d/$f.orig" ]; then cp -v "$d/$f.orig" "$d/$f"; else echo "no original of $f saved; untouched"; fi
  done
  echo "Restart Sunshine to apply: $(sunshine_restart_hint)"
}

case "${1:-help}" in
  connectors) cmd_connectors ;;
  gen)        cmd_gen ;;
  kargs)      cmd_kargs ;;
  install)    cmd_install ;;
  uninstall)  cmd_uninstall ;;
  reload)     cmd_reload ;;
  on)         cmd_on ;;
  off)        cmd_off ;;
  status)     cmd_status ;;
  login)      cmd_login ;;
  profile)    shift; cmd_profile "${1:-}" ;;
  sunshine)   cmd_sunshine ;;
  sunshine-restore) cmd_sunshine_restore ;;
  *) cat <<EOF
Usage: virtual-monitor.sh <command>
  connectors        list DRM connectors and which one will be used
  profile NAME      switch device profile (profiles/*.conf)
  gen               build the EDID into build/ (no root)
  kargs             print the kernel parameters install would add
  install           [sudo] install EDID + kernel params + initrd config   (then reboot)
  reload            [sudo] re-apply a changed EDID live, no reboot (best effort)
  uninstall         [sudo] remove everything install added
  on / off          only the virtual monitor active / restore physical monitors  (GNOME)
  status            show what is configured and what the desktop sees
  login             make the GDM login screen use only the physical monitors      (GNOME/GDM)
  sunshine          add the 'Virtual Monitor' app + KMS/VAAPI settings to Sunshine
  sunshine-restore  put Sunshine's apps.json/sunshine.conf back as they were
Config: $CONF
EOF
  ;;
esac

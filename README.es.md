# virtual-monitor

[English](README.md) · Traducción del README principal con notas de la máquina donde se desarrolló.

Monitor virtual para **GNOME/Wayland + Sunshine/Moonlight**, para usar la tablet como pantalla
mientras los monitores físicos quedan apagados. Configurable para cualquier dispositivo.

## Cómo funciona

1. **Conector fantasma.** Se elige un conector DRM libre de la iGPU AMD (`CONNECTOR=auto` elige uno; en el desarrollo fue `DP-4`) y se le
   dice al kernel que está "conectado" (`video=DP-4:e`) con un EDID que *nosotros* generamos
   (`drm.edid_firmware=DP-4:edid/virtual-monitor.bin`). GNOME lo ve como un monitor real con la
   resolución de la tablet.
2. **Solo el virtual.** `virtual-monitor.sh on` deshabilita los monitores físicos con `gdctl`
   (quedan en standby/negro) y deja activo únicamente el virtual. `off` restaura el layout guardado.
3. **Sunshine** captura por KMS ese monitor y codifica con VAAPI en la iGPU. Una app de Sunshine
   ("Virtual Monitor") ejecuta `on` al conectar y `off` al desconectar.

**Por qué en la AMD y no en la NVIDIA:** el driver propietario de NVIDIA ignora `drm.edid_firmware`.
Como la captura KMS entrega buffers de la iGPU, se codifica con VAAPI (Vega: H.264/HEVC sobra para 2000x1200).

## Estructura

```
virtual-monitor.sh        CLI principal
config/virtual-monitor.conf   configuración activa (editable)
profiles/*.conf           perfiles listos: tab-s6-lite, fullhd-60, ipad-pro-11, phone-landscape
lib/gen-edid.py           generador de EDID 1.4 (CVT-RB, sin dependencias)
lib/gnome-layout.py       guarda/aplica/restaura el layout de monitores vía gdctl
build/                    EDID generado

Estado fuera del proyecto:
~/.local/state/virtual-monitor/layout.json   layout guardado por `on`, usado por `off`
~/.config/sunshine/*.orig                    copias originales de Sunshine (para `sunshine-restore`)
~/.config/monitors.xml                       layout persistente de GNOME (lo escribe `login`)
/var/lib/gdm3/.config/monitors.xml           layout del login de GDM (lo copia `login`)
```

## Instalación (una vez)

```bash
cd ~/programs/virtual-monitor
./virtual-monitor.sh connectors      # confirma que el conector elegido está "disconnected" y en driver=amdgpu
./virtual-monitor.sh profile tab-s6-lite   # (ya es el activo)
sudo ./virtual-monitor.sh install    # EDID + parámetro de kernel + hook initramfs + update-grub
sudo reboot
./virtual-monitor.sh status          # debe decir "virtual monitor: <nombre> (mode 2000x1200@60...)"
./virtual-monitor.sh sunshine        # añade la app y fija capture=kms, encoder=vaapi, adapter_name
systemctl --user restart app-dev.lizardbyte.app.Sunshine
./virtual-monitor.sh login           # con los monitores físicos activos: arregla el login gris (ver abajo)
```

Opcional, para que Sunshine arranque solo al iniciar sesión:
`systemctl --user enable --now app-dev.lizardbyte.app.Sunshine` (su estado por defecto es *disabled*).

Tras el primer reinicio, el login de GDM puede salir en el monitor virtual y dejar los físicos en gris
(todavía no se ha ejecutado `login`). Si pasa, escribe usuario y contraseña a ciegas (Enter, contraseña, Enter).

`install` solo **crea** archivos nuevos, no edita los existentes:
`/usr/lib/firmware/edid/virtual-monitor.bin`, `/etc/default/grub.d/99-virtual-monitor.cfg`,
`/etc/initramfs-tools/hooks/virtual-monitor-edid` y `/etc/dracut.conf.d/99-virtual-monitor.conf`.
Este sistema genera el initrd con **dracut** (aunque se invoque `update-initramfs`), por eso hay ambos archivos;
el de dracut es el que cuenta. Comprobar que el EDID quedó dentro:
`sudo lsinitrd /boot/initrd.img-$(uname -r) | grep edid`. `uninstall` los borra y regenera grub/initramfs.
GRUB no se edita: solo se añade un drop-in en `/etc/default/grub.d/` y no hay que elegir ninguna entrada especial.
Las entradas de recuperación no llevan los parámetros, así que sirven de arranque limpio.

## Pantalla de login en gris / escritorio extendido al virtual

Al arrancar, GDM no conocía ningún layout para 3 monitores y puso el login en el virtual (los físicos en gris);
además GNOME sumaba el virtual al escritorio en cada sesión.
Se arregla una vez, **con la sesión de streaming cerrada y los monitores físicos encendidos**
(si hay streaming activo, `login` se niega porque no hay monitor físico que guardar):

```bash
./virtual-monitor.sh login     # guarda el layout sin el virtual y lo copia a GDM (pide sudo)
```

Hace dos cosas: persiste en `~/.config/monitors.xml` un layout con el virtual deshabilitado (así tampoco se
suma al escritorio en cada sesión) y copia ese archivo a `/var/lib/gdm3/.config/monitors.xml`.
Para deshacerlo: `sudo rm /var/lib/gdm3/.config/monitors.xml`. Si cambias de monitores físicos, repítelo.
Si alguna vez aun así te quedas sin imagen en el login, escribe usuario y contraseña a ciegas (Enter, contraseña, Enter).

## Referencia de comandos

| Comando | Root | Qué hace |
|---|---|---|
| `connectors` | no | lista conectores DRM, su estado y driver |
| `profile NOMBRE` | no | activa un perfil de `profiles/` |
| `gen` | no | genera el EDID en `build/` e indica qué fabricante mostrará GNOME |
| `install` | sí | instala EDID, parámetros de kernel y configuración de initrd (reiniciar después) |
| `reload` | sí | reaplica un EDID cambiado sin reiniciar (best effort, vía debugfs) |
| `uninstall` | sí | quita todo lo que añadió `install` |
| `on` / `off` | no | deja solo el virtual activo / restaura los monitores físicos |
| `login` | pide sudo | persiste el layout sin el virtual y lo copia a GDM |
| `kargs` | no | imprime los parámetros de kernel (para gestores de arranque manuales) |
| `status` | no | configuración, parámetros de kernel y lo que ve GNOME |
| `sunshine` | no | añade la app «Virtual Monitor» y fija KMS + VAAPI en Sunshine |
| `sunshine-restore` | no | devuelve la configuración original de Sunshine |

`on`, `off`, `login` y `status` usan `gdctl`, así que necesitan una sesión gráfica de GNOME (no un tty).
El monitor virtual se localiza por el nombre de producto del EDID (`NAME`), no por el nombre del conector
ni por el fabricante, así que sigue encontrándose aunque cambies `VENDOR` antes de reiniciar.

## Restaurar Sunshine

`./virtual-monitor.sh sunshine-restore` devuelve `apps.json` y `sunshine.conf` a como estaban antes
(copias originales `*.orig` en `~/.config/sunshine/`, creadas una sola vez en la primera ejecución de `sunshine`).
Reinicia Sunshine después.

## Uso diario

En Moonlight, conecta a Sunshine y lanza la app **Virtual Monitor**. Al empezar se apagan los
monitores físicos; al terminar la sesión se restauran. Manualmente: `./virtual-monitor.sh on` / `off`.

## Cambiar de dispositivo / resolución

Todo sale de `config/virtual-monitor.conf` (tu configuración activa, ignorada por git). Se crea la primera vez
a partir de la plantilla documentada [`config/virtual-monitor.conf.example`](config/virtual-monitor.conf.example):

| Variable | Significado |
|---|---|
| `CONNECTOR` | conector a falsear (libre, no NVIDIA) o `auto` (primer DP libre, luego HDMI, luego otros) |
| `WIDTH` `HEIGHT` `REFRESH` | resolución nativa y Hz del cliente |
| `EXTRA_MODE` | segunda resolución opcional (`1920x1080@60`) |
| `DIAGONAL_INCH` | tamaño físico, solo orientativo para el escalado |
| `NAME` | nombre del producto en el EDID (≤13 caracteres, sin espacios); así se encuentra el monitor |
| `VENDOR` | código de fabricante de 3 letras. GNOME muestra «fabricante + pulgadas» (p. ej. `VMN 11"`); si el código está en `/usr/share/hwdata/pnp.ids` muestra su nombre (`LNX` = The Linux Foundation), si no, las 3 letras |
| `SCALE` | escala de GNOME al estar activo (1, 1.25, 1.5, 2) |

```bash
./virtual-monitor.sh profile ipad-pro-11   # o edita el .conf a mano
sudo ./virtual-monitor.sh install && sudo reboot
# alternativa sin reiniciar (best effort, requiere debugfs):
sudo ./virtual-monitor.sh reload
```

**Cambiar el nombre mostrado:** edita `VENDOR` (GNOME no admite texto libre, solo el nombre de fabricante de la tabla PNP),
aplica con `sudo ./virtual-monitor.sh install` + reinicio (o `reload`) y vuelve a ejecutar `./virtual-monitor.sh login`,
porque GNOME identifica los monitores guardados por fabricante.

Para un dispositivo nuevo: copia un perfil en `profiles/`, ajusta los valores y actívalo con `profile`.
Para que Moonlight use exactamente esa resolución, pon el mismo valor en sus ajustes de streaming.
Si el cliente pide otra resolución, Moonlight reescala (no cambia el monitor).

## Notas y problemas conocidos

- **Estado de la verificación (2026-10):** probado de extremo a extremo en esta máquina (Ubuntu, GNOME 50
  Wayland, RTX 3060 + Vega): conector forzado tras reiniciar, EDID dentro del initrd, `on`/`off`,
  Sunshine con KMS + VAAPI y streaming a una Galaxy Tab S6 Lite. **Sin verificar tras reiniciar:** `login`
  (arreglo del login gris) y el cambio de `VENDOR` por defecto a `VMN`.
- **DVI** tiene límite de ~165 MHz de reloj; 2000x1200@60 usa ~160 MHz, justo. `auto` prefiere DP y HDMI.
  Para resoluciones altas evita DVI.
- **Sin pantalla física activa**, GNOME puede bloquear/suspender por inactividad: desactiva la
  suspensión automática mientras uses la tablet (Configuración → Energía).
- **`output_name` de Sunshine:** si Sunshine captura el monitor equivocado, mira en
  `~/.config/sunshine/sunshine.log` los displays detectados y fija `output_name` en `sunshine.conf`.
  Si Sunshine elige NVENC en vez de VAAPI, confirma `encoder = vaapi` (lo escribe `sunshine`).
- **VAAPI:** si la codificación falla, instala `mesa-va-drivers` y `vainfo` y prueba
  `vainfo --display drm --device /dev/dri/renderD129`.
- **Si algo sale mal al arrancar:** en GRUB pulsa `e`, borra `video=DP-4:e drm.edid_firmware=...`
  de la línea `linux` y arranca con F10; luego `sudo ./virtual-monitor.sh uninstall`.
- **`gdctl failed ... needs a running GNOME session`:** estás en un tty o en el login; inicia sesión gráfica.
- **Las ventanas se van al monitor virtual:** significa que está habilitado fuera del streaming; ejecuta
  `login` (persiste el layout con el virtual deshabilitado) o `off`.
- El AV1 de VAAPI falla en la Vega (aparece como error en el log de Sunshine); es normal, usa H.264/HEVC.
- Los monitores deshabilitados con `gdctl` entran en standby (negro), no se cortan eléctricamente.
- El layout guardado vive en `~/.local/state/virtual-monitor/layout.json`.

## Alternativa por hardware

Un *dummy plug* HDMI/DP (~5-10 €) en un puerto libre de la NVIDIA hace lo mismo sin configurar el
kernel y permite codificar con NVENC. Si esta solución da problemas, es el plan B más fiable.

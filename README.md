# Kulan OS

A GNOME desktop operating system built on Debian 13 ("trixie"), branded for Kulan Group.

Kulan OS boots from an ISO file, runs as a live system, and installs to a disk with a
graphical installer. It builds for **ARM64** (Apple Silicon Macs in UTM) and **x86-64**
(regular PCs, bootable from a USB stick).

## What's inside

| Area | Details |
| --- | --- |
| Base | Debian 13, with Debian security updates |
| Desktop | GNOME 48 with a macOS-style look: frosted-glass top bar and overview (Blur my Shell), floating translucent dock with running-app dots, window buttons on the left, tray icons for apps like Slack and Zoom |
| Look | Inter font, JetBrains Mono for code, Papirus icons, dark style with teal accent, layered wave wallpaper (light and dark) |
| Boot | Kulan OS splash with spinner; installed systems skip the text menu (hold Esc while starting to see it) |
| Branding | Boot menu, splash, login screen, wallpaper, Settings > About, installer, console banner |
| Apps | Firefox ESR, Files, Console, Text Editor, Calculator, Image Viewer, Document Viewer, Archive Manager, Disks, System Monitor |
| App store | GNOME Software with Debian packages and Flathub (Spotify, Zoom, Slack, VS Code...) |
| Security | Firewall on at boot (blocks incoming, allows outgoing); automatic daily security updates |
| Installer | Calamares, rebranded as "Install Kulan OS" |
| VMs | Clipboard sharing and display resizing in UTM/QEMU |

## 1. Put the project on GitHub

GitHub's free build machines compile the ISOs for you, so you don't need a Linux
computer.

On your Mac, open Terminal:

```bash
# Move the project out of Downloads, then delete the zip
unzip ~/Downloads/kulan-os.zip -d ~/Projects
rm ~/Downloads/kulan-os.zip

cd ~/Projects/kulan-os
git init -b main
git add .
git commit -m "Kulan OS 1.0"
```

Then create the repository and push. With the GitHub CLI (`brew install gh`, then `gh auth login`):

```bash
gh repo create kulan-os --private --source=. --push
```

Or without it: create an empty repository named `kulan-os` on github.com, then:

```bash
git remote add origin https://github.com/<your-username>/kulan-os.git
git push -u origin main
```

> Public repositories build for free with no limit. Private repositories use your
> account's included Actions minutes; one build of both ISOs takes roughly 60-90 minutes.

## 2. Download the ISO

1. On github.com, open the repository and click **Actions**.
2. Open the latest **Build Kulan OS** run. It takes 30-60 minutes.
3. When it shows green, scroll to **Artifacts** and download **kulan-os-arm64** (for your Mac).
4. Unzip it, then delete the downloaded zip:

```bash
unzip ~/Downloads/kulan-os-arm64.zip -d ~/Projects/kulan-os-iso
rm ~/Downloads/kulan-os-arm64.zip
```

To rebuild without pushing changes, use **Actions > Build Kulan OS > Run workflow** and
pick which ISO to build. Artifacts are kept for 14 days.

To verify the download: `cd ~/Projects/kulan-os-iso && shasum -a 256 -c *.sha256`

## 3. Run it on your Mac (UTM)

1. Install UTM from [mac.getutm.app](https://mac.getutm.app) (free).
2. Click **+ > Virtualize > Linux**.
3. Under **Boot ISO Image**, click **Browse** and choose `kulan-os-1.0-arm64.iso`.
4. Memory: **4096 MB**, CPU cores: **4**. Storage: **32 GB**. Skip shared folders.
5. Name it **Kulan OS**, click **Save**, then press **Play**.
6. Choose **Kulan OS Live (arm64)** in the boot menu. The desktop opens automatically.
7. Click **Install Kulan OS** in the dock and follow the steps.
8. When the installer finishes, shut down the VM. In UTM, select the VM, open the
   **CD/DVD** drive menu and choose **Clear**, then press **Play** again.

In the live session the user is `kulan` and the password is `live`.

## Customizing

| Change | Where |
| --- | --- |
| Name, version, website, live user | `kulan.conf` (then run `python3 scripts/render-assets.py`) |
| Apps installed | `config/package-lists/kulan-desktop.list.chroot` (one Debian package per line) |
| Logo and wallpaper | `assets/*.svg` (then run `python3 scripts/render-assets.py`) |
| GNOME defaults (fonts, icons, dock) | `config/includes.chroot/usr/share/glib-2.0/schemas/90_kulan-os.gschema.override` |
| Boot splash | `config/includes.chroot/usr/share/kulan/plymouth/kulan.plymouth` |
| Boot menu on installed systems | `config/includes.chroot/etc/default/grub.d/kulan-os.cfg` |
| Dock apps | `config/hooks/live/0200-kulan-desktop.hook.chroot` |
| Firewall and updates | `config/hooks/live/0400-kulan-security.hook.chroot` |

Commit and push; GitHub builds a new ISO automatically. Changes to only `assets/`,
`scripts/` or Markdown files don't trigger a build.

Rendering the images needs `rsvg-convert` and Pillow: `brew install librsvg` and `pip3 install pillow` on a Mac.

## Building locally (optional)

On a Debian 13 machine or VM of the same architecture as the ISO you want:

```bash
sudo ./build.sh          # or: sudo ./build.sh arm64 / amd64
```

The ISO and a `.sha256` checksum land in the project folder; the full log is in `build.log`.

## Project layout

```
kulan.conf                 Name, version and other identity settings
auto/                      live-build entry points (config, build, clean)
config/package-lists/      Debian packages in the image
config/includes.chroot/    Files copied into the system (wallpapers, icons, defaults)
config/hooks/live/         Build-time scripts: branding, desktop, installer, security, Flathub
assets/                    SVG sources for the logo and wallpapers
scripts/render-assets.py   Turns the SVGs into the PNGs under config/
ci/build-in-container.sh   What GitHub runs inside a Debian 13 container
.github/workflows/         The GitHub Actions build (ARM64 + x86-64)
```

## Notes

- **Secure Boot.** The installed system's EFI boot entry keeps the name "Debian". Debian's
  signed bootloader only works from that folder, so renaming it would break Secure Boot.
  Everything users see says Kulan OS.
- **os-release.** `ID=kulan`, with `ID_LIKE=debian` and `VERSION_CODENAME=trixie`, so
  install scripts for Debian software keep working. The file is protected with a dpkg
  diversion, so Debian updates don't overwrite it.
- **Hidden boot menu.** If you dual-boot, hold Esc while the computer starts to pick
  another system, or set `GRUB_TIMEOUT_STYLE=menu` in
  `config/includes.chroot/etc/default/grub.d/kulan-os.cfg`.
- **Firewall.** Incoming connections are blocked. To allow one, for example SSH:
  `sudo ufw allow ssh`.
- **Based on Debian.** Kulan OS is an unofficial Debian derivative and is not endorsed by
  the Debian project.

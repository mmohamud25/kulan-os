# Kulan OS

A GNOME desktop operating system built on Debian 13 ("trixie"), branded for Kulan Group.

Kulan OS boots from an ISO file, runs as a live system, and installs to a disk with a
graphical installer. It builds for **ARM64** (Apple Silicon Macs in UTM) and **x86-64**
(regular PCs, bootable from a USB stick).

## What's inside

| Area | Details |
| --- | --- |
| Base | Debian 13, with Debian stable and security updates |
| Desktop | GNOME 48 with a macOS-style look: frosted-glass top bar and overview (Blur my Shell), floating translucent dock with running-app dots, window buttons on the left, tray icons for apps like Slack and Zoom |
| Kulan Shell | Kulan's own GNOME extension: rounded navy menus and quick settings with teal highlights, an update icon in the top bar with a count of waiting updates, and a notice after each update |
| App grid | Apps sorted into folders: Office, Internet, Media, Developer, Utilities, System |
| Look | Inter font, JetBrains Mono for code, Adwaita icons (each app’s own icon), Bibata cursor, dark style with teal accent, layered wave wallpaper (light and dark) |
| Welcome app | Replaces GNOME's own tour. Shown once at first login: light or dark mode, accent color, one-click installs of popular apps (only those that run on this computer), the developer kit, and a security overview |
| Update Center | Shows waiting updates (security ones marked), installs them with a progress bar, What's new for each release, recent update history, automatic updates on or off, and restore points |
| Restore points | Timeshift snapshots of the system (not your files): daily and weekly, plus one before updates if you choose. Skipped when the disk is low on space |
| Boot | Kulan OS splash with spinner; installed systems skip the text menu (hold Esc while starting to see it) |
| Branding | Boot menu, splash, login screen, wallpaper, Settings > About, installer, console banner |
| Apps | Firefox ESR, LibreOffice (Writer, Calc, Impress), VLC, GIMP, Files, Console, Text Editor, Calculator, Image Viewer, Document Viewer, Archive Manager, Disks, System Monitor |
| Developer kit | Optional, one click in the Welcome app: Git, Python (pip, venv), build tools, Docker and Docker Compose, curl |
| App store | GNOME Software with Debian packages and Flathub |
| Security | Firewall on at boot (blocks incoming, allows outgoing) with a settings app; automatic daily updates; disk encryption ticked by default in the installer; Tor Browser launcher on PCs |
| Privacy | No telemetry; Wi-Fi hides your device's hardware address; Firefox blocks trackers and sends no usage data; old trash and temporary files are cleared after 30 days |
| Backups | Deja Dup (back up to a drive or cloud storage) |
| Updates | The Kulan update channel: your own signed package repository, so installed systems get Kulan fixes and new versions through normal updates. Older installs upgrade themselves to the latest version |
| Build number | Each ISO is named with its build, e.g. `kulan-os-2.1-build9-arm64.iso`; Settings > About and Update Center show it too |
| Installer | Calamares, rebranded as "Install Kulan OS" |
| VMs | Clipboard sharing and display resizing in UTM/QEMU |

> **Apps on ARM.** Some popular apps (Spotify, Zoom, Slack, Discord, Chrome) are only built
> for PCs. On an Apple Silicon Mac's VM, the Welcome app greys them out; use their websites.

## 1. Put the project on GitHub

GitHub's free build machines compile the ISOs for you, so you don't need a Linux
computer.

On your Mac, open Terminal:

```bash
# Move the project out of Downloads, then delete the zip
unzip ~/Downloads/kulan-os.zip -d ~/Projects
rm ~/Downloads/kulan-os.zip

cd ~/Projects/kulan-os
git push -u origin main
```

The project is already committed and linked to `github.com/mmohamud25/kulan-os`. If GitHub
asks you to sign in, run `gh auth login` first (`brew install gh` installs it).

> Public repositories build for free with no limit. Private repositories use your
> account's included Actions minutes; one build of both ISOs takes about 30 minutes.

## 2. Download the ISO

1. On github.com, open the repository and click **Actions**.
2. Open the latest **Build Kulan OS** run.
3. When it shows green, scroll to **Artifacts** and download **kulan-os-arm64** (for your Mac).
4. Unzip it, then delete the downloaded zip:

```bash
unzip ~/Downloads/kulan-os-arm64.zip -d ~/Projects/kulan-os-iso
rm ~/Downloads/kulan-os-arm64.zip
```

**If the browser blocks the download** (Chrome sometimes calls ISO zips "dangerous"),
download the latest successful build from Terminal instead. No zip to delete:

```bash
RUN=$(gh run list -R mmohamud25/kulan-os -w build-iso.yml -b main -s success -L 1 --json databaseId -q '.[0].databaseId')
gh run download "$RUN" -R mmohamud25/kulan-os -n kulan-os-arm64 -D ~/Projects/kulan-os-iso
ls ~/Projects/kulan-os-iso
```

Every build first installs the Kulan packages in a clean Debian 13 system and tests them,
then opens each ISO and checks that Kulan's parts are really inside (branding,
boot splash, installer settings, firewall, Flathub and more). The results are in the run's
**Summary**; a failed check fails the build.

To rebuild without pushing changes, use **Actions > Build Kulan OS > Run workflow** and
pick which ISO to build. Artifacts are kept for 14 days.

To verify the download: `cd ~/Projects/kulan-os-iso && shasum -a 256 -c *.sha256`

## 3. Run it on your Mac (UTM)

1. Install UTM from [mac.getutm.app](https://mac.getutm.app) (free).
2. Click **+ > Virtualize > Linux**.
3. Under **Boot ISO Image**, click **Browse** and choose the `kulan-os-…-arm64.iso` you downloaded.
4. Memory: **4096 MB**, CPU cores: **4**. Storage: **32 GB**. Skip shared folders.
5. Name it **Kulan OS**, click **Save**, then press **Play**.
6. Choose **Kulan OS Live (arm64)** in the boot menu. The desktop opens automatically.
7. Click **Install Kulan OS** in the dock and follow the steps. **Encrypt system** is
   ticked: choose a password you won't forget, because it can't be recovered. At every
   start, the Kulan splash asks for it before the login screen.
8. When the installer shows **All done**, untick **Restart now**, click **Done** and shut
   down the VM. Then detach the ISO, or the VM starts the installer again: in UTM, click
   **Edit**, select the drive under **Drives** that holds the ISO (often **USB Drive**),
   click **Delete** (your ISO file is kept) and **Save**. Press **Play** again.

**Best display setting:** in UTM, click **Edit > Display** and choose
**virtio-ramfb-gl** (GPU supported). With it you see the Kulan splash and the disk password
prompt from the very start of every boot.

If a VM window says **"Display output is not active"**, the screen has gone to sleep or
the system is waiting for the disk password: click inside the window and press **Shift**,
or type the disk password and press **Enter**.

**Restarting in UTM:** UTM can freeze the VM's screen when Kulan OS restarts (for example
after updates). If the window stays on a still picture, choose **Shut Down** instead of
Restart, then press **Play** in UTM. Real computers aren't affected.

In the live session the user is `kulan` and the password is `live`.

**Running from an external SSD:** create the VM, quit UTM, then move `Kulan OS.utm` from
`~/Library/Containers/com.utmapp.UTM/Data/Documents` to the SSD and open it with
**File > Open**. Shut the VM down before unplugging the SSD.

## 4. Turn on the update channel (one time)

Kulan's own parts are real Debian packages (`kulan-base`, `kulan-desktop-settings`,
`kulan-welcome`, `kulan-update-center`, plus the optional `kulan-apps` and `kulan-devkit`). With the update channel on, every installed Kulan OS fetches new versions
of them automatically, signed with a key only you hold.

On your Mac:

```bash
brew install gnupg gh      # skip what you already have
gh auth login              # if you haven't already
cd ~/Projects/kulan-os
scripts/setup-update-channel.sh
```

The script creates the signing key, stores it as a GitHub secret, turns on GitHub Pages and
saves a backup of the key in your home folder. Then:

```bash
git add packages/kulan-archive-keyring.gpg
git commit -m "Add the Kulan OS update signing key"
git push
```

Store the backup (`~/kulan-os-signing-key-BACKUP.asc`) in a password manager, then delete it
with `rm ~/kulan-os-signing-key-BACKUP.asc`.

From then on, each push to `main` goes through four stages: build the Kulan packages,
install and test them in a clean Debian 13 system, publish them to
`https://mmohamud25.github.io/kulan-os/apt/`, then build and inspect the ISOs. A failure at
any stage stops the later ones. ISOs built after setup include the channel; installed
systems pick up updates in their daily automatic update, or right away from Update Center.

## Releasing a new version

New work goes on a branch first (for example `kulan-os-2.0`). Builds of a branch test the
packages and build the ISOs, but **don't publish** to the update channel, so no installed
system changes. To release, merge the branch's pull request into `main` on GitHub. The
`main` build publishes the packages, waits until the channel serves them, then builds the
ISOs. Installed systems update within a day.

## Customizing

| Change | Where |
| --- | --- |
| Name, version, website, live user | `kulan.conf` (then run `python3 scripts/render-assets.py`) |
| Apps installed | `config/package-lists/kulan-desktop.list.chroot` (one Debian package per line) |
| Logo and wallpaper | `assets/*.svg` (then run `python3 scripts/render-assets.py`) |
| GNOME defaults (fonts, icons, dock, privacy) | `packages/kulan-desktop-settings/root/usr/share/glib-2.0/schemas/90_kulan-os.gschema.override` |
| Dock apps and extensions | `packages/kulan-desktop-settings/root/usr/lib/kulan/desktop-setup` |
| Boot splash | `packages/kulan-desktop-settings/root/usr/share/kulan/plymouth/kulan.plymouth` |
| Boot menu on installed systems | `packages/kulan-desktop-settings/root/etc/default/grub.d/kulan-os.cfg` |
| Welcome app (pages, app list) | `packages/kulan-welcome/root/usr/bin/kulan-welcome` |
| Top bar, menus, update icon | `packages/kulan-desktop-settings/root/usr/share/gnome-shell/extensions/kulan-shell@kulangroup.com/` |
| App grid folders | `packages/kulan-desktop-settings/root/etc/dconf/db/local.d/10-kulan-app-folders` |
| Update Center, release notes | `packages/kulan-update-center/` (notes: `root/usr/share/kulan/release-notes.json`) |
| Restore point schedule | `packages/kulan-update-center/root/usr/lib/kulan/restore-points` |
| Optional office/media and developer apps | `packages/kulan-apps/`, `packages/kulan-devkit/` |
| Firewall, privacy, Firefox defaults | `packages/kulan-base/` |
| Installer encryption default | `config/includes.chroot/etc/calamares/modules/partition.conf` |

Commit and push; GitHub builds new ISOs automatically and, once the update channel is on,
updates installed systems too. Changes to only `assets/`, `scripts/render-assets.py` or
Markdown files don't trigger a build.

Rendering the images needs `rsvg-convert` and Pillow: `brew install librsvg` and
`pip3 install pillow` on a Mac.

## Building locally (optional)

On a Debian 13 machine or VM of the same architecture as the ISO you want:

```bash
sudo ./build.sh          # or: sudo ./build.sh arm64 / amd64
```

The ISO and a `.sha256` checksum land in the project folder; the full log is in `build.log`.

## Project layout

```
kulan.conf                    Name, version, update channel and other settings
packages/                     Kulan's own Debian packages
  build-debs.sh                 Builds them into dist/debs/
  kulan-base/                   Identity, update channel, security and privacy defaults
  kulan-desktop-settings/       Look, wallpapers, dock, login screen, boot splash
  kulan-welcome/                The welcome app
  kulan-update-center/          Update Center, update status, restore points
  kulan-apps/                   Office and media apps (installed in the ISO)
  kulan-devkit/                 Developer kit (optional, not in the ISO)
auto/                         live-build entry points (config, build, clean)
config/package-lists/         Debian packages in the image
config/includes.chroot/       Installer settings (encryption on by default)
config/hooks/live/            Build-time checks plus installer branding and Flathub
assets/                       SVG sources for the logo and wallpapers
scripts/render-assets.py      Turns the SVGs into the PNGs in kulan-desktop-settings
scripts/setup-update-channel.sh  One-time update channel setup
ci/                           GitHub build, ISO inspection and repository publishing
.github/workflows/            The GitHub Actions build (ARM64 + x86-64)
```

## Notes

- **Secure Boot.** The installed system's EFI boot entry keeps the name "Debian". Debian's
  signed bootloader only works from that folder, so renaming it would break Secure Boot.
  Everything users see says Kulan OS.
- **os-release.** `ID=kulan`, with `ID_LIKE=debian` and `VERSION_CODENAME=trixie`, so
  install scripts for Debian software keep working. `kulan-base` protects the file with a
  dpkg diversion, so Debian updates don't overwrite it; removing `kulan-base` restores
  Debian's.
- **Automatic updates.** Debian stable and security updates and Kulan updates install
  daily. Turn them off in Update Center. Kulan's
  own security defaults (like the firewall) are applied once, at first install, so a user's
  later choices survive updates.
- **Hidden boot menu.** If you dual-boot, hold Esc while the computer starts to pick
  another system, or set `GRUB_TIMEOUT_STYLE=menu` in
  `packages/kulan-desktop-settings/root/etc/default/grub.d/kulan-os.cfg`.
- **Firewall.** Incoming connections are blocked. To allow one, open **Firewall
  Configuration** or run, for example, `sudo ufw allow ssh`.
- **Restore points.** They cover the system, not your home folder (use Backups for that).
  To roll back, open **Update Center > Restore points > Manage**. In the live session
  restore points are off.
- **License.** The packages say "All rights reserved" until you choose a license.
- **Based on Debian.** Kulan OS is an unofficial Debian derivative and is not endorsed by
  the Debian project.

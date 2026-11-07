# Kulan OS 2.14: every app looks and behaves like Kulan

Started from what Chrome looked like on the ThinkPad after 2.13.

## Done (branch kulan-os-2.14)
- **Chrome-type browsers on Chrome's own colours.** 2.12 gave Chrome, Chromium,
  Brave and Edge Kulan's look (their GTK theme) only where no theme was set, so
  a colour picked in Customize Chrome, or synced from another computer, kept
  Chrome's colours and its Windows-style buttons. 2.14 sets the GTK theme once
  more over Chrome's own colours (the colour is kept, for anyone who goes back
  to Classic); never over a Web Store or the Qt theme, never while the browser
  is open, and once only.
- **Windows under the dock.** An app could open a window bigger than the screen
  or partly off it (Chrome opens at its last size): its bottom ran under the
  dock. Kulan Shell (windowfit.js) moves such a window, and makes it smaller if
  needed, to fit between the top bar and the dock, when it opens only.
- **Debian's package servers over HTTPS.** Installed computers fetched Debian's
  packages over plain HTTP (Debian's default). An APT flaw in November 2026
  (CVE-2026-109518, fixed in APT 3.0.4) let someone changing the traffic on the
  way swap a package. New computers use HTTPS from the start (auto/config's
  --mirror-binary); computers from before move when they update (kulan-base's
  apt-https, once): only deb.debian.org and security.debian.org, never another
  mirror someone chose, and not with an APT proxy (package caches are HTTP
  only). kulan-base now depends on ca-certificates.
- **Oldest allowed versions.** ci/min-versions.txt: an ISO with a Debian package
  older than a known serious fix fails the build, even when Debian's security
  tracker can't be reached (ci/vuln-scan.sh then passes with a warning). First
  entries: the kernel's TCP flaw (CVE-2026-98432, 6.12.113-1) and APT's.

## Tests added
- ci/browser-look-test.py: Chrome's own colours get the GTK theme once, even
  where 2.12 had already been; Qt and Web Store themes left alone.
- ci/window-fit-test.js: where a window goes to fit (under the dock, too tall,
  bigger than the screen, off to the side, second screen).
- Boot test: a window asking for 5000x5000 opens to fit the screen, in the
  live session.
- ci/apt-https-test.sh (package tests): what apt-https changes and leaves alone.
- Boot test and upgrade test: Debian's sources are HTTPS only, and the update
  check works with them (a new computer, and one updated from an older release).
- ci/min-versions.sh in the ISO job.

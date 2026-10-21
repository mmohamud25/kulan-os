#!/bin/bash
# Render the theme that makes older (GTK 3) apps look like the rest of
# Kulan OS: adw-gtk3 (LGPL-2.1, https://github.com/lassekongo83/adw-gtk3),
# compiled at a pinned release, plus small "Kulan-<accent>" themes on top
# of it that follow the accent colour chosen in Kulan Settings.
#
#   npm install sass && scripts/render-gtk3-theme.sh   (needs git and sass)
set -euo pipefail

TAG=v6.5
COMMIT=34a69db97369475e931b10ce699d0be2307a5def
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/packages/kulan-desktop-settings/root/usr/share/themes"
SASS="${SASS:-sass}"
SRC="$(mktemp -d)"
trap 'rm -rf "${SRC}"' EXIT

git -C "${SRC}" init -q
git -C "${SRC}" fetch -q --depth 1 https://github.com/lassekongo83/adw-gtk3 "${COMMIT}"
git -C "${SRC}" checkout -q FETCH_HEAD
test "$(git -C "${SRC}" rev-parse HEAD)" = "${COMMIT}"

rm -rf "${OUT}"/adw-gtk3 "${OUT}"/adw-gtk3-dark "${OUT}"/Kulan-*
for VARIANT in light dark; do
	NAME=adw-gtk3
	[[ "${VARIANT}" == dark ]] && NAME=adw-gtk3-dark
	D="${OUT}/${NAME}"
	install -d "${D}/gtk-3.0/assets" "${D}/gtk-4.0/assets"
	if [[ "${VARIANT}" == light ]]; then
		"${SASS}" --no-source-map --style=expanded --silence-deprecation=if-function "${SRC}/src/sass/gtk.scss" "${D}/gtk-3.0/gtk.css"
	else
		"${SASS}" --no-source-map --style=expanded --silence-deprecation=if-function "${SRC}/src/sass/gtk-dark.scss" "${D}/gtk-3.0/gtk.css"
	fi
	"${SASS}" --no-source-map --style=expanded --silence-deprecation=if-function "${SRC}/src/sass/gtk-dark.scss" "${D}/gtk-3.0/gtk-dark.css"
	"${SASS}" --no-source-map --style=expanded --silence-deprecation=if-function "${SRC}/src/sass/gtk4/libadwaita-tweaks.scss" "${D}/gtk-4.0/libadwaita-tweaks.css"
	cp "${SRC}"/src/theme-light/gtk4/*.css "${D}/gtk-4.0/"
	cp "${SRC}"/src/assets/* "${D}/gtk-3.0/assets/"
	for A in bullet check dash devel; do cp "${SRC}/src/assets/${A}-symbolic.svg" "${D}/gtk-4.0/assets/"; done
	sed -e "s/@VariantThemeName@/${NAME}/g" -e "s/@ThemeName@/adw-gtk3/g" "${SRC}/src/index.theme.in" > "${D}/index.theme"
	cp "${SRC}/LICENSE" "${D}/LICENSE"
	printf 'adw-gtk3 %s (commit %s), https://github.com/lassekongo83/adw-gtk3, LGPL-2.1-or-later.\n' "${TAG}" "${COMMIT}" > "${D}/SOURCE"
done

# GNOME's accent colours (libadwaita 1.6+), so older apps match the accent
# chosen in Kulan Settings.
declare -A ACCENTS=([blue]=3584e4 [teal]=2190a4 [green]=3a944a [yellow]=c88800 [orange]=ed5b00
	[red]=e62d42 [pink]=d56199 [purple]=9141ac [slate]=6f8396)
for ACCENT in "${!ACCENTS[@]}"; do
	for VARIANT in light dark; do
		NAME="Kulan-${ACCENT}"
		BASE=adw-gtk3
		if [[ "${VARIANT}" == dark ]]; then NAME+=-dark; BASE+=-dark; fi
		D="${OUT}/${NAME}"
		install -d "${D}/gtk-3.0" "${D}/gtk-4.0"
		for GTK in gtk-3.0 gtk-4.0; do
			printf '/* Kulan OS: %s with the %s accent. */\n@import url("../../%s/%s/gtk.css");\n@define-color accent_bg_color #%s;\n@define-color accent_color mix(@accent_bg_color, %s, 0.17);\n' \
				"${BASE}" "${ACCENT}" "${BASE}" "${GTK}" "${ACCENTS[${ACCENT}]}" \
				"$([[ "${VARIANT}" == dark ]] && echo white || echo black)" > "${D}/${GTK}/gtk.css"
		done
		# GTK 4 reads the accent from CSS variables, which adw-gtk3 sets to blue.
		printf ':root {\n  --accent-bg-color: #%s;\n  --accent-color: color-mix(in srgb, #%s 83%%, %s);\n}\n' \
			"${ACCENTS[${ACCENT}]}" "${ACCENTS[${ACCENT}]}" \
			"$([[ "${VARIANT}" == dark ]] && echo white || echo black)" >> "${D}/gtk-4.0/gtk.css"
		printf '[Desktop Entry]\nType=X-GNOME-Metatheme\nName=%s\nComment=adw-gtk3 with the %s accent, for Kulan OS\nEncoding=UTF-8\n\n[X-GNOME-Metatheme]\nGtkTheme=%s\n' \
			"${NAME}" "${ACCENT}" "${NAME}" > "${D}/index.theme"
	done
done
echo "Rendered adw-gtk3 ${TAG} and $(find "${OUT}" -maxdepth 1 -name 'Kulan-*' | wc -l) accent themes into ${OUT}"

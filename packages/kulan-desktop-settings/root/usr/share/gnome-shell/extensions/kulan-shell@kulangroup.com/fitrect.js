// Kulan Shell: where a window goes to fit the space apps may use (windowfit.js).
// No GNOME imports, so the tests can run it on its own (ci/window-fit-test.js).

export function fitRect(rect, area) {
    // The window's new place and size inside the area (made smaller if it's
    // bigger), or null if it already fits.
    const inside = rect.x >= area.x && rect.y >= area.y &&
        rect.x + rect.width <= area.x + area.width && rect.y + rect.height <= area.y + area.height;
    if (inside)
        return null;
    const width = Math.min(rect.width, area.width);
    const height = Math.min(rect.height, area.height);
    return {
        x: Math.min(Math.max(rect.x, area.x), area.x + area.width - width),
        y: Math.min(Math.max(rect.y, area.y), area.y + area.height - height),
        width,
        height,
    };
}

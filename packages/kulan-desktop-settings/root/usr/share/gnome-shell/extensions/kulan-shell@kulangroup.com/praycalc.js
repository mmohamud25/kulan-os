// Prayer times, calculated on this computer from the sun's position (the
// standard astronomical method used by Muslim calendars). No imports, so
// it can be tested on its own.
//
// times(date, latitude, longitude, utcOffsetHours, settings) returns hours
// of the day (local time, 0-24) for fajr, sunrise, dhuhr, asr, maghrib, isha.

// fajr / isha as sun angles below the horizon, or isha as minutes after maghrib.
export const METHODS = {
    MWL: {name: 'Muslim World League', fajr: 18, isha: 17},
    ISNA: {name: 'Islamic Society of North America', fajr: 15, isha: 15},
    Egypt: {name: 'Egyptian General Authority of Survey', fajr: 19.5, isha: 17.5},
    Makkah: {name: 'Umm al-Qura University, Makkah', fajr: 18.5, ishaMinutes: 90},
    Karachi: {name: 'University of Islamic Sciences, Karachi', fajr: 18, isha: 18},
    Tehran: {name: 'Institute of Geophysics, University of Tehran', fajr: 17.7, isha: 14, maghrib: 4.5, shia: true},
    Jafari: {name: 'Shia Ithna Ashari (Jafari)', fajr: 16, isha: 14, maghrib: 4, shia: true},
    Gulf: {name: 'Gulf Region', fajr: 19.5, ishaMinutes: 90},
    Kuwait: {name: 'Kuwait', fajr: 18, isha: 17.5},
    Qatar: {name: 'Qatar', fajr: 18, ishaMinutes: 90},
    Singapore: {name: 'Singapore, Malaysia and Indonesia', fajr: 20, isha: 18},
    Turkey: {name: 'Diyanet, Turkey', fajr: 18, isha: 17},
};
export const PRAYERS = ['fajr', 'sunrise', 'dhuhr', 'asr', 'maghrib', 'isha'];

const rad = d => d * Math.PI / 180;
const deg = r => r * 180 / Math.PI;
const fix = (a, b) => {
    const r = a - b * Math.floor(a / b);
    return r < 0 ? r + b : r;
};
const fixHour = h => fix(h, 24);

function julian(year, month, day) {
    if (month <= 2) {
        year -= 1;
        month += 12;
    }
    const A = Math.floor(year / 100);
    const B = 2 - A + Math.floor(A / 4);
    return Math.floor(365.25 * (year + 4716)) + Math.floor(30.6001 * (month + 1)) + day + B - 1524.5;
}

// Declination and equation of time for a Julian day.
function sun(jd) {
    const D = jd - 2451545.0;
    const g = fix(357.529 + 0.98560028 * D, 360);
    const q = fix(280.459 + 0.98564736 * D, 360);
    const L = fix(q + 1.915 * Math.sin(rad(g)) + 0.020 * Math.sin(rad(2 * g)), 360);
    const e = 23.439 - 0.00000036 * D;
    const RA = deg(Math.atan2(Math.cos(rad(e)) * Math.sin(rad(L)), Math.cos(rad(L)))) / 15;
    return {
        declination: deg(Math.asin(Math.sin(rad(e)) * Math.sin(rad(L)))),
        equation: q / 15 - fixHour(RA),
    };
}

export function times(date, latitude, longitude, utcOffset, settings = {}) {
    const method = METHODS[settings.method] ?? METHODS.MWL;
    const asrFactor = settings.asr === 'hanafi' ? 2 : 1;
    const jDate = julian(date.year, date.month, date.day) - longitude / (15 * 24);

    const midDay = t => fixHour(12 - sun(jDate + t).equation);
    const sunAngleTime = (angle, t, before) => {
        const decl = sun(jDate + t).declination;
        const noon = midDay(t);
        const cosT = (-Math.sin(rad(angle)) - Math.sin(rad(decl)) * Math.sin(rad(latitude))) /
            (Math.cos(rad(decl)) * Math.cos(rad(latitude)));
        if (cosT < -1 || cosT > 1)
            return NaN; // the sun never gets that low (or high) today
        const T = deg(Math.acos(cosT)) / 15;
        return noon + (before ? -T : T);
    };
    const asrTime = t => {
        const decl = sun(jDate + t).declination;
        const angle = -deg(Math.atan(1 / (asrFactor + Math.tan(rad(Math.abs(latitude - decl))))));
        return sunAngleTime(angle, t, false);
    };

    // First guesses (as fractions of a day), refined once with the results.
    let t = {fajr: 5, sunrise: 6, dhuhr: 12, asr: 13, maghrib: 18, isha: 18};
    for (let i = 0; i < 2; i++) {
        const d = Object.fromEntries(Object.entries(t).map(([k, v]) => [k, (Number.isFinite(v) ? v : 12) / 24]));
        t = {
            fajr: sunAngleTime(method.fajr, d.fajr, true),
            sunrise: sunAngleTime(0.833, d.sunrise, true),
            dhuhr: midDay(d.dhuhr),
            asr: asrTime(d.asr),
            maghrib: sunAngleTime(method.maghrib ?? 0.833, d.maghrib, false),
            isha: method.ishaMinutes ? NaN : sunAngleTime(method.isha, d.isha, false),
        };
    }
    const sunset = sunAngleTime(0.833, t.maghrib / 24, false);
    if (method.ishaMinutes)
        t.isha = t.maghrib + method.ishaMinutes / 60;

    // Far north or south, the sun may not go low enough for fajr or isha:
    // use a share of the night instead (the "angle-based" rule).
    const night = 24 - (sunset - t.sunrise);
    const fajrPortion = method.fajr / 60 * night;
    if (!Number.isFinite(t.fajr) || t.sunrise - t.fajr > fajrPortion)
        t.fajr = t.sunrise - fajrPortion;
    if (!method.ishaMinutes) {
        const ishaPortion = method.isha / 60 * night;
        if (!Number.isFinite(t.isha) || t.isha - sunset > ishaPortion)
            t.isha = sunset + ishaPortion;
    }

    const zone = utcOffset - longitude / 15;
    const out = {};
    for (const name of PRAYERS) {
        let h = t[name] + zone;
        // Small safety margins, as printed timetables do.
        if (name === 'dhuhr')
            h += 1 / 60;
        if (name === 'maghrib' && !method.maghrib)
            h += 1 / 60;
        out[name] = fixHour(h);
    }
    return out;
}

// Suggested method for a country (ISO 3166 code), as local mosques use.
export function methodForCountry(code) {
    const c = (code ?? '').toUpperCase();
    if (['US', 'CA'].includes(c))
        return 'ISNA';
    if (c === 'SA')
        return 'Makkah';
    if (['EG', 'SO', 'DJ', 'SD', 'SS', 'LY', 'DZ', 'MA', 'TN', 'SY', 'LB', 'JO', 'IQ', 'MY'].includes(c))
        return c === 'MY' ? 'Singapore' : 'Egypt';
    if (['PK', 'IN', 'BD', 'AF'].includes(c))
        return 'Karachi';
    if (c === 'IR')
        return 'Tehran';
    if (c === 'TR')
        return 'Turkey';
    if (['SG', 'ID', 'BN'].includes(c))
        return 'Singapore';
    if (c === 'KW')
        return 'Kuwait';
    if (c === 'QA')
        return 'Qatar';
    if (['AE', 'OM', 'BH'].includes(c))
        return 'Gulf';
    return 'MWL';
}

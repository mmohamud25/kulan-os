// Render the city list Kulan Settings > Prayer Times searches, from GeoNames
// data (CC BY 4.0, https://www.geonames.org) as packaged by the
// all-the-cities npm package (MIT). Cities of 15,000 people or more.
//
//   npm install all-the-cities@3.1.0 && node scripts/render-cities.mjs
//
// Output: packages/kulan-settings/root/usr/share/kulan/cities.tsv.gz with
// name, country code, country name, population, latitude, longitude.
import {createRequire} from 'node:module';
import {writeFileSync} from 'node:fs';
import {gzipSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const require = createRequire(path.join(process.cwd(), 'x.js'));
const cities = require('all-the-cities');
const countries = new Intl.DisplayNames(['en'], {type: 'region'});
const rows = cities
    .filter(c => c.population >= 15000 && c.name && c.country)
    .sort((a, b) => b.population - a.population || a.name.localeCompare(b.name))
    .map(c => [c.name.replace(/\t/g, ' '), c.country, countries.of(c.country) ?? c.country, c.population,
        c.loc.coordinates[1].toFixed(4), c.loc.coordinates[0].toFixed(4)].join('\t'));
const header = '# Cities of 15,000 people or more. Data: GeoNames (CC BY 4.0, https://www.geonames.org).\n' +
    '# name\tcountry\tcountry name\tpopulation\tlatitude\tlongitude\n';
const out = path.join(path.dirname(fileURLToPath(import.meta.url)), '..',
    'packages/kulan-settings/root/usr/share/kulan/cities.tsv.gz');
// mtime 0 in the gzip header: the same input always gives the same file.
writeFileSync(out, gzipSync(header + rows.join('\n') + '\n', {level: 9}));
console.log(`${rows.length} cities -> ${out}`);

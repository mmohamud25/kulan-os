// Tests Kulan Shell's fitRect (windowfit.js): a window bigger than the space
// apps may use, or partly off it (under the dock), is moved and made smaller
// to fit; one that fits is left alone.
//
//   gjs -m ci/window-fit-test.js [path/to/fitrect.js]
import {exit, programArgs} from 'system';

const path = programArgs[0] ?? '/usr/share/gnome-shell/extensions/kulan-shell@kulangroup.com/fitrect.js';
const {fitRect} = await import(`file://${path}`);

// 1920x1080 screen: top bar 32 high, dock 64 high at the bottom.
const area = {x: 0, y: 32, width: 1920, height: 984};
let failed = 0;
function check(name, got, want) {
    if (JSON.stringify(got) !== JSON.stringify(want)) {
        print(`window fit: FAILED: ${name}: ${JSON.stringify(got)}, wanted ${JSON.stringify(want)}`);
        failed++;
    }
}
check('fits: left alone', fitRect({x: 100, y: 100, width: 800, height: 600}, area), null);
check('exactly the space: left alone', fitRect({x: 0, y: 32, width: 1920, height: 984}, area), null);
check('bottom under the dock: moved up', fitRect({x: 8, y: 500, width: 1600, height: 600}, area),
    {x: 8, y: 416, width: 1600, height: 600});
check('taller than the space: made shorter', fitRect({x: 8, y: 0, width: 1612, height: 1026}, area),
    {x: 8, y: 32, width: 1612, height: 984});
check('bigger than the screen: the whole space', fitRect({x: -50, y: -50, width: 5000, height: 5000}, area),
    {x: 0, y: 32, width: 1920, height: 984});
check('off to the right: moved in', fitRect({x: 1500, y: 100, width: 800, height: 600}, area),
    {x: 1120, y: 100, width: 800, height: 600});
check('second screen', fitRect({x: 1920, y: 900, width: 800, height: 600}, {x: 1920, y: 0, width: 1280, height: 1024}),
    {x: 1920, y: 424, width: 800, height: 600});
if (failed)
    exit(1);
print('window fit: every case passed');

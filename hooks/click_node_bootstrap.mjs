// Observation starts before the entry point. A failed observer must not strand
// the requested check: top-level await has a bounded, ordinary-execution exit.
import inspector from 'node:inspector';
import fs from 'node:fs';
import path from 'node:path';
import { isMainThread } from 'node:worker_threads';

const directory = process.env.CLICK_NODE_OBSERVER_DIRECTORY;
const ownedOptions = process.env.NODE_OPTIONS === '--import=' + import.meta.url;
// A package script can conceal `node --test` from the outer argv parser. Node
// marks its isolated test children too; activating an inspector there can
// silently serialize the user's scheduler.
const testRunner = process.env.NODE_TEST_CONTEXT || process.execArgv.some(arg => arg === '--test' || arg.startsWith('--test='));
const priorPreload = process.execArgv.some(arg => arg === '-r' || arg.startsWith('--require') || arg.startsWith('--loader') || arg.startsWith('--experimental-loader'));
if (directory && ownedOptions && isMainThread && !testRunner && !priorPreload) {
  let owned = false;
  try {
    if (!inspector.url()) {
      inspector.open(0, '127.0.0.1', false);
      owned = true;
      const descriptor = fs.openSync(path.join(directory, 'endpoints.pipe'), 'w');
      try {
        fs.writeSync(descriptor, JSON.stringify({ pid: process.pid, url: inspector.url() }) + '\n');
      } finally { fs.closeSync(descriptor); }
      const acknowledgement = path.join(directory, `ready-${process.pid}`);
      const attached = await new Promise(resolve => {
        let attempts = 0;
        const timer = setInterval(() => {
          if (fs.existsSync(acknowledgement) || ++attempts >= 250) {
            clearInterval(timer);
            resolve(fs.existsSync(acknowledgement));
          }
        }, 20);
      });
      if (!attached) inspector.close();
    }
  } catch {
    if (owned) { try { inspector.close(); } catch {} }
  }
}

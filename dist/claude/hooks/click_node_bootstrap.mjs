// Observation starts before the entry point. A failed observer must not strand
// the requested check: top-level await has a bounded, ordinary-execution exit.
import inspector from 'node:inspector';
import fs from 'node:fs';
import path from 'node:path';
import { isMainThread, threadId } from 'node:worker_threads';

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
      // One announcement file per process: a plain create-and-write that
      // every host supports, which the collector polls for. A FIFO would
      // need a POSIX-only mkfifo and a rename would be an extra file event
      // for the projection to explain.
      fs.writeFileSync(path.join(directory, `endpoint-${process.pid}.json`),
        JSON.stringify({ pid: process.pid, url: inspector.url() }) + '\n', { mode: 0o600 });
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
      // The boundary between the runtime's own bootstrap reads and the
      // check's inputs, as one file event every backend can see; strace
      // also has the acknowledgement probe above, ETW has only this.
      else fs.writeFileSync(path.join(directory, `started-${process.pid}`), '', { mode: 0o600 });
    }
  } catch {
    if (owned) { try { inspector.close(); } catch {} }
  }
} else if (directory && ownedOptions && !isMainThread) {
  // A Worker isolate runs outside the Inspector session above, so its clock,
  // random and other runtime facilities are unobserved. Announce it, so the
  // collector counts Workers explicitly instead of relying on the timing of
  // the isolate's own cgroup probe to reject the projection.
  try {
    fs.writeFileSync(path.join(directory, `worker-${process.pid}-${threadId}`), '');
  } catch {}
}

'use strict';
// Preloaded (`--require` through NODE_OPTIONS) into every Node process and
// worker thread of an observed check on hosts without a system call tracer
// (Windows). It writes what the process read, listed, looked up and did not
// find, created, changed and deleted to its own log in CLICK_INPUT_LOG: one
// line the first time each (event, path) happens, written synchronously, so a
// killed process or a terminated worker loses nothing it did before.
//
// Node's module loader resolves and reads through internal bindings that no
// patch sees; module hooks report what it loaded, and the lookups resolution
// makes are recorded from the specifier, as the loader would make them.
//
// Line format: `<hrtime>\t<event>\t<operation>\t<JSON path>`. Events: I input,
// M missing, P produced, O opened for update (created when absent), U input
// changed in place, T touched, D deleted, X an operation this file cannot
// account for; sockets: B a socket or pipe the process listens on, S one it
// connected to, N a network host it looked up or connected to (`host:port`).
// The first line is a JSON header naming the process.

const LOG = process.env.CLICK_INPUT_LOG;
const INSTALLED = Symbol.for('click.inputObserver');

if (LOG && !globalThis[INSTALLED]) {
  globalThis[INSTALLED] = true;
  install();
}

function install() {
  const fs = require('node:fs');
  const path = require('node:path');
  const { fileURLToPath } = require('node:url');
  const { threadId } = require('node:worker_threads');
  const Module = require('node:module');
  const plain = {
    openSync: fs.openSync, writeSync: fs.writeSync, existsSync: fs.existsSync,
    lstatSync: fs.lstatSync, readdirSync: fs.readdirSync,
  };
  let descriptor;
  try {
    descriptor = plain.openSync(path.join(LOG, `${process.pid}-${threadId}.log`), 'a');
  } catch {
    return;
  }
  const clock = process.hrtime.bigint;
  const seen = new Set();

  function write(line) {
    try {
      plain.writeSync(descriptor, line);
    } catch {
      // The log directory is gone: the observer has already given up.
    }
  }

  write(JSON.stringify({
    pid: process.pid, ppid: process.ppid, thread: threadId, exec: process.execPath,
    argv: process.argv, cwd: process.cwd(), hooks: typeof Module.registerHooks === 'function',
    at: String(clock()),
  }) + '\n');

  function note(event, operation, target) {
    if (target == null) return;
    const key = event + operation + '\0' + target;
    if (seen.has(key)) return;
    seen.add(key);
    write(`${clock()}\t${event}\t${operation}\t${JSON.stringify(target)}\n`);
  }

  function toPath(value) {
    if (typeof value === 'string') return value;
    if (Buffer.isBuffer(value)) return value.toString('utf8');
    if (value instanceof URL) return value.protocol === 'file:' ? fileURLToPath(value) : null;
    return null; // a descriptor or a FileHandle: its path was noted when it was opened
  }

  function absolute(value) {
    const text = toPath(value);
    return text ? path.resolve(text) : null;
  }

  function absent(error) {
    return Boolean(error) && (error.code === 'ENOENT' || error.code === 'ENOTDIR');
  }

  // -- what each call means ---------------------------------------------------

  // A lookup: found, not found, or failed some other way (still an input).
  function looked(operation) {
    return {
      ok(args) { note('I', operation, absolute(args[0])); },
      fail(args, error) { note(absent(error) ? 'M' : 'I', absent(error) ? operation : 'metadata', absolute(args[0])); },
    };
  }
  const read = looked('read');
  const metadata = looked('metadata');
  const statLike = {
    ok(args, result) {
      // stat(path, { throwIfNoEntry: false }) returns undefined for a missing path.
      note(result === undefined && args[1] && args[1].throwIfNoEntry === false ? 'M' : 'I', 'metadata', absolute(args[0]));
    },
    fail: metadata.fail,
  };
  const realpath = {
    ok(args, result) {
      note('I', 'metadata', absolute(args[0]));
      if (typeof result === 'string') note('I', 'metadata', result);
    },
    fail: metadata.fail,
  };
  const listing = {
    ok(args, result) {
      const directory = absolute(args[0]);
      note('I', 'enumerate', directory);
      const options = args[1];
      if (directory && options && typeof options === 'object' && options.recursive && Array.isArray(result)) {
        for (const entry of result) {
          const name = typeof entry === 'string' ? entry : entry && entry.parentPath != null
            ? path.join(entry.parentPath, entry.name) : null;
          if (name == null) continue;
          const full = path.resolve(directory, name);
          note('I', 'enumerate', path.dirname(full));
          try {
            if (plain.lstatSync(full).isDirectory()) note('I', 'enumerate', full);
          } catch {
            // removed meanwhile: its parent's listing already changed
          }
        }
      }
    },
    fail: metadata.fail,
  };

  const O = fs.constants;
  function flagsOf(flags) {
    if (typeof flags === 'number') return flags;
    switch (flags == null ? 'r' : String(flags)) {
      case 'r': case 'rs': case 'sr': return O.O_RDONLY;
      case 'r+': case 'rs+': case 'sr+': return O.O_RDWR;
      case 'w': return O.O_TRUNC | O.O_CREAT | O.O_WRONLY;
      case 'wx': case 'xw': return O.O_TRUNC | O.O_CREAT | O.O_WRONLY | O.O_EXCL;
      case 'w+': return O.O_TRUNC | O.O_CREAT | O.O_RDWR;
      case 'wx+': case 'xw+': return O.O_TRUNC | O.O_CREAT | O.O_RDWR | O.O_EXCL;
      case 'a': case 'as': case 'sa': return O.O_APPEND | O.O_CREAT | O.O_WRONLY;
      case 'ax': case 'xa': return O.O_APPEND | O.O_CREAT | O.O_WRONLY | O.O_EXCL;
      case 'a+': case 'as+': case 'sa+': return O.O_APPEND | O.O_CREAT | O.O_RDWR;
      case 'ax+': case 'xa+': return O.O_APPEND | O.O_CREAT | O.O_RDWR | O.O_EXCL;
      default: return -1;
    }
  }

  // The same reading of open flags as the system call tracer's.
  function opened(target, flags) {
    const value = flagsOf(flags);
    if (value < 0) { note('X', 'open-flags', target); return; }
    const writes = (value & (O.O_WRONLY | O.O_RDWR | O.O_CREAT | O.O_TRUNC | O.O_APPEND)) !== 0;
    const creates = (value & O.O_CREAT) !== 0;
    const truncates = (value & O.O_TRUNC) !== 0;
    const exclusive = creates && (value & O.O_EXCL) !== 0;
    const reads = (value & O.O_RDWR) !== 0 || (value & O.O_WRONLY) === 0;
    if (!writes) note('I', 'read', target);
    else if (creates && (truncates || exclusive || !reads)) note('P', '', target);
    else if (reads && creates) note('O', '', target);
    else if (reads) note('U', 'read', target);
    else note('T', '', target);
  }

  // open(path, flags) takes the flags itself; readFile, writeFile and
  // appendFile take them as `flag` in an options object (a string there is
  // the encoding).
  function opener(pick, defaultFlags) {
    return {
      ok(args) {
        opened(absolute(args[0]), pick(args) ?? defaultFlags);
      },
      fail(args, error) {
        note(absent(error) ? 'M' : 'I', absent(error) ? 'read' : 'metadata', absolute(args[0]));
      },
    };
  }
  const optionFlag = (at) => (args) => {
    const options = args[at];
    return options && typeof options === 'object' ? options.flag : undefined;
  };
  const openCall = opener((args) => args[1], 'r');
  const readFile = opener(optionFlag(1), 'r');
  const writeFile = opener(optionFlag(2), 'w');
  const appendFile = opener(optionFlag(2), 'a');
  const produced = { ok(args) { note('P', '', absolute(args[0])); }, fail() {} };
  const touched = { ok(args) { note('T', '', absolute(args[0])); }, fail: metadata.fail };
  const deleted = { ok(args) { note('D', '', absolute(args[0])); }, fail: metadata.fail };
  const mkdir = {
    ok(args, result) {
      // Recursive mkdir returns the first directory it created, if any.
      const options = args[1];
      const recursive = options && typeof options === 'object' && options.recursive;
      if (!recursive) note('P', '', absolute(args[0]));
      else if (typeof result === 'string') note('P', '', result);
      else note('I', 'metadata', absolute(args[0]));
    },
    fail: metadata.fail,
  };
  const mkdtemp = { ok(_args, result) { if (typeof result === 'string') note('P', '', result); }, fail() {} };
  const rename = {
    ok(args) { note('D', '', absolute(args[0])); note('P', '', absolute(args[1])); },
    fail: metadata.fail,
  };
  const copyFile = {
    ok(args) { note('I', 'read', absolute(args[0])); note('P', '', absolute(args[1])); },
    fail: read.fail,
  };
  const link = {
    ok(args) { note('I', 'metadata', absolute(args[0])); note('P', '', absolute(args[1])); },
    fail: metadata.fail,
  };
  const symlink = { ok(args) { note('P', '', absolute(args[1])); }, fail() {} };
  const exists = {
    ok(args, result) { note(result ? 'I' : 'M', 'metadata', absolute(args[0])); },
    fail() {},
  };
  const unaccounted = (name) => ({
    ok(args) { note('X', name, absolute(args[0]) ?? '?'); },
    fail(args) { note('X', name, absolute(args[0]) ?? '?'); },
  });

  const meanings = {
    access: metadata, stat: statLike, lstat: statLike, statfs: metadata,
    readlink: metadata, realpath: realpath,
    readFile, open: openCall, opendir: listing, readdir: listing,
    writeFile, appendFile, truncate: touched,
    chmod: touched, lchmod: touched, chown: touched, lchown: touched, utimes: touched, lutimes: touched,
    mkdir, mkdtemp, rename, copyFile, link, symlink,
    rm: deleted, rmdir: deleted, unlink: deleted,
    cp: unaccounted('cp'), glob: unaccounted('glob'), openAsBlob: read,
  };

  // -- wrapping -----------------------------------------------------------------

  function copyProperties(from, to) {
    for (const key of Reflect.ownKeys(from)) {
      if (key === 'length' || key === 'name' || key === 'prototype') continue;
      try {
        Object.defineProperty(to, key, Object.getOwnPropertyDescriptor(from, key));
      } catch {
        // non-configurable: leave it
      }
    }
    return to;
  }

  function wrapSync(target, name, meaning) {
    const original = target[name];
    if (typeof original !== 'function') return;
    target[name] = copyProperties(original, function (...args) {
      let result;
      try {
        result = Reflect.apply(original, this, args);
      } catch (error) {
        meaning.fail(args, error);
        throw error;
      }
      meaning.ok(args, result);
      return result;
    });
  }

  function wrapCallback(target, name, meaning) {
    const original = target[name];
    if (typeof original !== 'function') return;
    target[name] = copyProperties(original, function (...args) {
      const last = args.length - 1;
      const callback = args[last];
      if (typeof callback === 'function') {
        const given = args.slice(0, last);
        args[last] = function (error, ...rest) {
          if (error) meaning.fail(given, error);
          else meaning.ok(given, rest[0]);
          return Reflect.apply(callback, this, [error, ...rest]);
        };
      }
      return Reflect.apply(original, this, args);
    });
  }

  function wrapPromise(target, name, meaning) {
    const original = target[name];
    if (typeof original !== 'function') return;
    target[name] = copyProperties(original, function (...args) {
      let promise;
      try {
        promise = Reflect.apply(original, this, args);
      } catch (error) {
        meaning.fail(args, error);
        throw error;
      }
      if (!promise || typeof promise.then !== 'function') {
        meaning.ok(args, promise);
        return promise;
      }
      return promise.then(
        (value) => { meaning.ok(args, value); return value; },
        (error) => { meaning.fail(args, error); throw error; },
      );
    });
  }

  for (const [name, meaning] of Object.entries(meanings)) {
    wrapSync(fs, name + 'Sync', meaning);
    wrapCallback(fs, name, meaning);
    wrapPromise(fs.promises, name, meaning);
  }
  wrapSync(fs, 'existsSync', exists);
  const nativeRealpath = fs.realpathSync.native;
  if (typeof nativeRealpath === 'function') {
    wrapSync(fs.realpathSync, 'native', realpath);
  }
  if (fs.realpath && typeof fs.realpath.native === 'function') {
    wrapCallback(fs.realpath, 'native', realpath);
  }
  const existsCallback = fs.exists;
  if (typeof existsCallback === 'function') {
    fs.exists = copyProperties(existsCallback, function (target, callback) {
      return Reflect.apply(existsCallback, this, [target, function (result) {
        exists.ok([target], result);
        if (typeof callback === 'function') return callback(result);
      }]);
    });
  }

  const chdir = process.chdir;
  process.chdir = copyProperties(chdir, function (directory) {
    const result = Reflect.apply(chdir, this, [directory]);
    note('I', 'metadata', process.cwd());
    return result;
  });

  const dlopen = process.dlopen;
  process.dlopen = copyProperties(dlopen, function (module, filename, ...rest) {
    note('I', 'read', absolute(filename));
    return Reflect.apply(dlopen, this, [module, filename, ...rest]);
  });

  // -- sockets --------------------------------------------------------------------
  // What reached a service outside the command makes its record volatile, as a
  // traced run's connect() would: a network host, or a socket or pipe that no
  // process of the command listens on.
  const net = require('node:net');
  const dns = require('node:dns');

  function target(args) {
    let first = args[0];
    if (Array.isArray(first)) first = first[0]; // the internal normalized form
    if (first && typeof first === 'object') {
      if (first.path != null) return { path: String(first.path) };
      return { host: first.host == null ? 'localhost' : String(first.host), port: first.port };
    }
    if (typeof first === 'string' && !/^\d+$/.test(first)) return { path: first };
    return { host: typeof args[1] === 'string' ? args[1] : 'localhost', port: first };
  }

  const connect = net.Socket.prototype.connect;
  net.Socket.prototype.connect = copyProperties(connect, function (...args) {
    try {
      const where = target(args);
      if (where.path != null) {
        const named = where.path.startsWith('\\\\') ? where.path : path.resolve(where.path);
        this.once('connect', () => note('S', '', named));
      } else {
        note('N', '', `${where.host}:${where.port}`);
      }
    } catch {
      note('X', 'connect', '?');
    }
    return Reflect.apply(connect, this, args);
  });

  const listen = net.Server.prototype.listen;
  net.Server.prototype.listen = copyProperties(listen, function (...args) {
    this.once('listening', () => {
      const address = this.address();
      if (typeof address === 'string') {
        note('B', '', address.startsWith('\\\\') ? address : path.resolve(address));
      }
    });
    return Reflect.apply(listen, this, args);
  });

  const resolving = (hostname) => {
    if (typeof hostname === 'string') note('N', '', `${hostname}:`);
  };
  for (const api of [dns, dns.promises]) {
    for (const name of ['lookup', 'resolve', 'resolve4', 'resolve6', 'resolveAny', 'resolveCname',
      'resolveMx', 'resolveNs', 'resolveSrv', 'resolveTxt']) {
      const original = api[name];
      if (typeof original !== 'function') continue;
      api[name] = copyProperties(original, function (hostname, ...rest) {
        resolving(hostname);
        return Reflect.apply(original, this, [hostname, ...rest]);
      });
    }
  }

  // Named imports of `node:fs` see the patched functions only after this.
  Module.syncBuiltinESMExports();

  // -- the module loader ----------------------------------------------------------

  const packageJson = new Map(); // directory -> nearest package.json at or above it, or null

  function nearestPackage(directory) {
    const walked = [];
    let current = directory;
    let found = null;
    for (;;) {
      if (packageJson.has(current)) {
        found = packageJson.get(current);
        break;
      }
      walked.push(current);
      const candidate = path.join(current, 'package.json');
      if (plain.existsSync(candidate)) {
        found = candidate;
        break;
      }
      note('M', 'metadata', candidate);
      const parent = path.dirname(current);
      if (parent === current) break;
      current = parent;
    }
    for (const item of walked) packageJson.set(item, found);
    if (found) note('I', 'read', found);
    return found;
  }

  function packageRoot(file) {
    // <...>/node_modules/[@scope/]name/<rest>
    const parts = file.split(path.sep);
    const index = parts.lastIndexOf('node_modules');
    if (index < 0 || index + 1 >= parts.length) return null;
    const width = parts[index + 1].startsWith('@') ? 2 : 1;
    return {
      root: parts.slice(0, index + 1 + width).join(path.sep),
      modules: parts.slice(0, index + 1).join(path.sep),
      name: parts.slice(index + 1, index + 1 + width).join('/'),
    };
  }

  function lookups(specifier, parentURL, resolvedURL, required) {
    if (typeof resolvedURL !== 'string' || !resolvedURL.startsWith('file:')) return;
    const resolved = fileURLToPath(resolvedURL);
    let parent = null;
    if (typeof parentURL === 'string' && parentURL.startsWith('file:')) parent = fileURLToPath(parentURL);
    const base = parent ? path.dirname(parent) : process.cwd();
    if (specifier.startsWith('./') || specifier.startsWith('../') || specifier === '.' || specifier === '..'
        || path.isAbsolute(specifier) || specifier.startsWith('file:')) {
      if (!required) return; // import resolves exactly the path it names
      // require tries the name with each extension and as a directory.
      const named = specifier.startsWith('file:') ? fileURLToPath(specifier) : path.resolve(base, specifier);
      note('I', 'enumerate', path.dirname(named));
      if (path.dirname(resolved) !== path.dirname(named)) note('I', 'enumerate', path.dirname(resolved));
      return;
    }
    if (specifier.startsWith('#')) {
      nearestPackage(base);
      return;
    }
    const owner = packageRoot(resolved);
    if (!owner) {
      nearestPackage(base); // a self-reference by package name
      return;
    }
    const packageFile = path.join(owner.root, 'package.json');
    if (plain.existsSync(packageFile)) note('I', 'read', packageFile);
    // Every node_modules between the importer and the one that had the package.
    const found = path.dirname(owner.modules);
    let current = base;
    for (;;) {
      if (current === found) break;
      if (path.basename(current) !== 'node_modules') {
        note('M', 'metadata', path.join(current, 'node_modules', ...owner.name.split('/')));
      }
      const up = path.dirname(current);
      if (up === current) break;
      current = up;
    }
  }

  if (typeof Module.registerHooks === 'function') {
    Module.registerHooks({
      resolve(specifier, context, nextResolve) {
        const result = nextResolve(specifier, context);
        try {
          const required = Array.isArray(context.conditions) && context.conditions.includes('require');
          lookups(specifier, context.parentURL, result && result.url, required);
        } catch {
          note('X', 'resolve', specifier);
        }
        return result;
      },
      load(url, context, nextLoad) {
        const result = nextLoad(url, context);
        if (typeof url === 'string' && url.startsWith('file:')) {
          try {
            const file = fileURLToPath(url);
            note('I', 'read', file);
            nearestPackage(path.dirname(file)); // the loader reads "type" from it
          } catch {
            note('X', 'load', url);
          }
        }
        return result;
      },
    });
  } else {
    note('X', 'module-hooks', process.execPath);
  }
}

'use strict';
// This process is outside the target tree. Targets never supply completion,
// counters or reuse authority. V8 reports calls to the original native functions,
// including saved references. A private value probe wraps selected functions
// to record the actual returned value without invoking the input twice.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const valueProbeSource = fs.readFileSync(path.join(__dirname, 'click_node_value_probe.js'), 'utf8');
const directory = process.argv[2];
const nativeStatePath = process.argv[3] || '';
const MAX_SESSIONS = 128;
const MAX_EVENTS = 200000;
const categories = ['clock', 'random', 'shared-memory', 'native-escape', 'inspector-access'];
const counts = Object.fromEntries(categories.map(name => [name, 0]));
const reasons = new Set();
const sockets = new Set();
const sessions = new Set();
const endpoints = new Set();
const processIds = new Set();
let events = 0, contexts = 0, installed = 0, completed = 0, workerCount = 0, stopping = false;
const sourceFile = 'click-observer-evaluation';
const valueSources = ['date-now', 'math-random', ...['add','and','compareExchange','exchange','load','notify','or','store','sub','wait','xor'].map(name => 'atomics-' + name)];
const valueRecords = Object.fromEntries(valueSources.map(source => [source, { count: 0, digest: '', last_value_digest: '', state_count: 0, last_state_digest: '' }]));
let valueCount = 0;
function hash(value) { return crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex'); }

function primitive(value, opaque = false) {
  if (!value || Object.keys(value).sort().join(',') !== 'type,value') return false;
  if (value.type === 'undefined' || opaque && value.type === 'opaque') return value.value === null;
  if (value.type === 'boolean') return typeof value.value === 'boolean';
  if (value.type === 'string') return typeof value.value === 'string' && value.value.length <= 1024;
  if (typeof value.value !== 'string') return false;
  if (value.type === 'bigint') return /^-?(?:0|[1-9][0-9]*)$/.test(value.value);
  return value.type === 'number' && (value.value === '-0' || String(Number(value.value)) === value.value);
}

function consumeValues(session, contextId, value) {
  if (!Array.isArray(value) || value.length !== 4) throw new Error('invalid values');
  const [rows, count, dropped, intact] = value;
  if (!rows || typeof rows !== 'object' || !Number.isSafeInteger(count) || count < 0 || count > 256 ||
      Object.keys(rows).length !== count || !Number.isSafeInteger(dropped) || dropped < 0) throw new Error('invalid values');
  if (dropped) failure('input-value-limit');
  if (intact !== null && typeof intact !== 'boolean') throw new Error('invalid values');
  if (intact === false) failure('input-source-replaced');
  if (intact !== null) session.valueClosed.add(contextId);
  for (let index = 0; index < count; index++) {
    const row = rows[index];
    if (!row || Object.keys(row).sort().join(',') !== 'memory,outcome,sequence,source,state,value' ||
        !valueSources.includes(row.source) || !Number.isSafeInteger(row.sequence) ||
        row.sequence !== (session.valueSequences.get(contextId) || 0) + 1 ||
        !['return', 'throw'].includes(row.outcome) || !primitive(row.value, row.outcome === 'throw') ||
        JSON.stringify(row).length > 2048 || !row.state ||
        Object.keys(row.state).sort().join(',') !== 'after,before' ||
        ![row.state.before, row.state.after].every(state => state === null || typeof state === 'string' &&
          /^(?:v8-xorshift128-cache64-v1:(?:[0-9]|[1-5][0-9]|6[0-4]):[0-9a-f]{16}:[0-9a-f]{16}:[0-9a-f]{64}|shared-bytes-sample-v1:[1-9][0-9]*:[0-9]+:[0-9a-f]{64})$/.test(state))) throw new Error('invalid values');
    const shared = row.source.startsWith('atomics-');
    if (row.memory !== null && (!shared || Object.keys(row.memory).sort().join(',') !== 'arguments,buffer,byteLength,byteOffset,index_argument' ||
        !Number.isSafeInteger(row.memory.buffer) || row.memory.buffer <= 0 ||
        !Number.isSafeInteger(row.memory.byteLength) || row.memory.byteLength < 0 ||
        !Number.isSafeInteger(row.memory.byteOffset) || row.memory.byteOffset < 0 || row.memory.byteOffset > row.memory.byteLength ||
        row.memory.index_argument !== null && !primitive(row.memory.index_argument) ||
        row.memory.arguments !== null && (typeof row.memory.arguments !== 'object' || Object.keys(row.memory.arguments).length > 8 ||
        Object.entries(row.memory.arguments).some(([key, value], index) => key !== String(index) || value !== null && !primitive(value)))))
      throw new Error('invalid shared values');
    for (const state of [row.state.before, row.state.after]) {
      if (state !== null && !(row.source === 'math-random' ? state.startsWith('v8-xorshift128-cache64-v1:') :
          shared && state.startsWith('shared-bytes-sample-v1:'))) throw new Error('invalid state source');
    }
    if (row.outcome === 'throw') failure('input-exception-state-incomplete');
    if (shared && (!row.memory || row.memory.arguments === null || Object.values(row.memory.arguments).some(value => value === null))) failure('input-coercion-state-incomplete');
    if ((shared || row.source === 'math-random') && (row.state.before === null || row.state.after === null)) failure('input-native-state-unavailable');
    if (row.source === 'math-random' && row.state.before !== null && row.state.after !== null) {
      const before = row.state.before.split(':'), after = row.state.after.split(':');
      const previous = session.randomStates.get(contextId);
      if (previous && previous !== row.state.before || Number(after[1]) !== (Number(before[1]) ? Number(before[1]) - 1 : 63) ||
          Number(before[1]) > 0 && before.slice(2).join(':') !== after.slice(2).join(':'))
        failure('input-random-state-discontinuity');
      session.randomStates.set(contextId, row.state.after);
    }
    session.valueSequences.set(contextId, row.sequence);
    if (++valueCount > MAX_EVENTS) { failure('input-value-limit'); return; }
    const record = valueRecords[row.source];
    record.count++;
    record.last_value_digest = hash(row.value);
    record.digest = hash([record.digest, session.ordinal, contextId, row]);
    if (row.state.before !== null && row.state.after !== null) {
      record.state_count++;
      record.last_state_digest = hash(row.state);
    }
  }
}

function failure(reason) { reasons.add(reason); }
function sendLine(value) { process.stdout.write(JSON.stringify(value) + '\n'); }

// Each expression resolves an original function in this execution context.
// Missing optional APIs are allowed; required primitive coverage is not.
const primitives = [
  ['clock', 'Date', true], ['clock', 'Date.now', true], ['random', 'Math.random', true],
  ['shared-memory', 'SharedArrayBuffer', true],
  ...['add','and','compareExchange','exchange','isLockFree','load','notify','or','store','sub','wait','waitAsync','xor']
    .map(name => ['shared-memory', `Atomics.${name}`, name !== 'waitAsync']),
  ['native-escape', 'WebAssembly.Module', true], ['native-escape', 'WebAssembly.Instance', true],
  ...['compile','compileStreaming','instantiate','instantiateStreaming','Memory']
    .map(name => ['native-escape', `WebAssembly.${name}`, false]),
  ['native-escape', 'fetch', false],
  ['native-escape', 'WeakRef', false], ['native-escape', 'FinalizationRegistry', false],
];
const modules = {
  net: { 'native-escape': ['Socket.prototype.connect', 'Server.prototype.listen'] },
  dgram: { 'native-escape': ['Socket.prototype.bind', 'Socket.prototype.send', 'Socket.prototype.connect'] },
  process: { clock: ['hrtime','hrtime.bigint','uptime','cpuUsage','resourceUsage','memoryUsage','availableMemory'],
             'native-escape': ['binding','_linkedBinding','dlopen'], 'inspector-access': ['_debugProcess','_debugEnd'] },
  perf_hooks: { clock: ['performance.now','performance.mark','performance.measure',
                         'Object.getOwnPropertyDescriptor(Object.getPrototypeOf(performance),"timeOrigin")?.get'] },
  crypto: { random: ['randomBytes','randomFill','randomFillSync','randomInt','randomUUID','getRandomValues',
                     'generateKey','generateKeySync','generateKeyPair','generateKeyPairSync',
                     'webcrypto.getRandomValues','webcrypto.randomUUID','webcrypto.subtle.generateKey'] },
  inspector: { 'inspector-access': ['open','close','Session.prototype.connect','Session.prototype.connectToMainThread'] },
  v8: { 'native-escape': ['setFlagsFromString','getHeapSnapshot','writeHeapSnapshot','queryObjects'] },
  vm: { realm: ['createContext','runInNewContext','Script.prototype.runInNewContext'] },
};

class Session {
  constructor(send, socket, parent = null, workerId = null) {
    this.sendRaw = send; this.socket = socket; this.parent = parent; this.workerId = workerId;
    this.pending = new Map(); this.nextId = 0; this.breakpoints = new Map(); this.children = new Map();
    this.contextIds = new Set(); this.scripts = new Map(); this.ready = false; this.finished = false;
    this.queue = Promise.resolve(); this.probing = 0;
    this.ordinal = sessions.size + 1;
    this.valueProbes = new Map(); this.valueSequences = new Map(); this.valueBreakpoints = new Map(); this.valueClosed = new Set();
    this.randomStates = new Map();
    sessions.add(this);
  }
  request(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.nextId;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('protocol-timeout ' + method)); }, 2500);
      this.pending.set(id, { resolve, reject, timer });
      try { this.sendRaw(JSON.stringify({ id, method, params })); }
      catch (error) { clearTimeout(timer); this.pending.delete(id); reject(error); }
    });
  }
  message(value) {
    if (++events > MAX_EVENTS) { failure('event-limit'); this.release(); return; }
    if (value.id) {
      const pending = this.pending.get(value.id);
      if (pending) {
        clearTimeout(pending.timer); this.pending.delete(value.id);
        value.error ? pending.reject(new Error(value.error.message || 'protocol-error')) : pending.resolve(value.result || {});
      }
      return;
    }
    const params = value.params || {};
    if (value.method === 'Debugger.scriptParsed') this.scripts.set(params.scriptId, params);
    else if (value.method === 'Runtime.executionContextCreated') {
      this.contextIds.add(params.context.id);
      if (this.ready && !this.realmBreakpoint) failure('context-start-unobserved');
    }
    else if (value.method === 'Debugger.paused') this.onPause(params);
    else if (value.method === 'NodeWorker.attachedToWorker') {
      if (sessions.size >= MAX_SESSIONS) { failure('session-limit'); this.release(); return; }
      workerCount++;
      if (!params.waitingForDebugger) failure('worker-start-unobserved');
      const child = new Session(message => this.request('NodeWorker.sendMessageToWorker',
        { sessionId: params.sessionId, message }).catch(() => failure('worker-transport-lost')), this.socket, this, params.sessionId);
      this.children.set(params.sessionId, child);
      child.initialize().then(() => child.request('Runtime.runIfWaitingForDebugger'))
        .catch(() => { failure('worker-setup-failed'); child.release(); });
    } else if (value.method === 'NodeWorker.receivedMessageFromWorker') {
      try { this.children.get(params.sessionId)?.message(JSON.parse(params.message)); }
      catch { failure('invalid-worker-message'); }
    } else if (value.method === 'NodeWorker.detachedFromWorker') {
      const child = this.children.get(params.sessionId);
      if (child && !child.finished) { failure('worker-completion-unobserved'); child.finish(); }
    } else if (value.method === 'NodeRuntime.waitingForDisconnect') {
      if (!this.completing) this.completing = this.finishValues().finally(() => { this.finish(); this.release(); });
    }
  }
  async install(contextId) {
    if (!this.installedContexts) this.installedContexts = new Map();
    if (this.installedContexts.has(contextId)) return this.installedContexts.get(contextId);
    const pending = this.installContext(contextId);
    this.installedContexts.set(contextId, pending);
    return pending;
  }
  async nativeForContext(contextId) {
    const bridge = await this.request('Runtime.evaluate', {
      expression: "Object.getOwnPropertyDescriptor(globalThis,'__click_native_state_bridge_v1')?.value",
      contextId, silent: true,
    });
    const object = bridge.result;
    // A sandbox may already own this name. Never delete its property or invoke
    // its accessors/proxies. Inspector reads the native function descriptions.
    if (!object?.objectId || object.type !== 'object' || object.subtype || object.className !== 'Object') return null;
    const properties = await this.request('Runtime.getProperties', { objectId: object.objectId, ownProperties: true });
    const functions = ['randomState', 'memoryState'].map(name => properties.result.find(item => item.name === name)?.value);
    if (functions.some((value, index) => value?.type !== 'function' || value.subtype ||
        value.description !== `function ${index ? 'clickMemoryState' : 'clickRandomState'}() { [native code] }`)) return null;
    const removed = await this.request('Runtime.callFunctionOn', { objectId: object.objectId,
      functionDeclaration: "function(){const current=Object.getOwnPropertyDescriptor(globalThis,'__click_native_state_bridge_v1');return current?.value===this && delete globalThis.__click_native_state_bridge_v1}",
      returnByValue: true, silent: true });
    return removed.result?.value === true ? functions.map(value => value.objectId) : null;
  }
  async installContext(contextId) {
    contexts++;
    if (contexts > 256) throw new Error('context limit');
    this.probing++;
    try {
      const entries = [...primitives];
      for (const [module, groups] of Object.entries(modules)) for (const [category, names] of Object.entries(groups)) {
        for (const name of names) {
          const expression = name.startsWith('Object.')
            ? `(function(){const {performance}=process.getBuiltinModule('perf_hooks');return ${name}})()`
            : `process.getBuiltinModule(${JSON.stringify(module)}).${name}`;
          entries.push([category, expression, false]);
        }
      }
      const expression = '[' + entries.map(([, expression]) => `(()=>{try{return ${expression}}catch{return undefined}})()`).join(',') + ']';
      const array = await this.request('Runtime.evaluate', {
        expression: expression + `\n//# sourceURL=${sourceFile}`, contextId, silent: true,
      });
      if (!array.result?.objectId || array.exceptionDetails) throw new Error('primitive resolution failed');
      const properties = await this.request('Runtime.getProperties', { objectId: array.result.objectId, ownProperties: true });
      const values = new Map(properties.result.map(property => [property.name, property.value]));
      await Promise.all(entries.map(async ([category, , required], index) => {
        if (counts[category]) return; // Presence is sticky; further calls add no reuse information.
        const value = values.get(String(index));
        if (value?.type !== 'function') {
          if (required) failure('primitive-unavailable');
          return;
        }
        try {
          const result = await this.request('Debugger.setBreakpointOnFunctionCall', { objectId: value.objectId });
          this.breakpoints.set(result.breakpointId, category);
        } catch (error) {
          // Aliases and functions sharing V8 code trigger an existing breakpoint.
          if (error.message !== 'Breakpoint at specified location already exists.') throw error;
        }
      }));
      const probe = await this.request('Runtime.evaluate', {
        expression: `(() => {${valueProbeSource}\nreturn createValueProbe();})()\n//# sourceURL=${sourceFile}`,
        contextId, silent: true,
      });
      if (!probe.result?.objectId || probe.exceptionDetails) throw new Error('value probe unavailable');
      const probeProperties = await this.request('Runtime.getProperties', { objectId: probe.result.objectId, ownProperties: true });
      const probeFunction = probeProperties.result.find(item => item.name === 'probe')?.value?.objectId;
      if (!probeFunction) throw new Error('value probe unavailable');
      const valueBreakpoint = await this.request('Debugger.setBreakpointOnFunctionCall', { objectId: probeFunction });
      this.valueBreakpoints.set(valueBreakpoint.breakpointId, contextId);
      this.valueProbes.set(contextId, probe.result.objectId);
      const nativeFunctions = await this.nativeForContext(contextId) || this.nativeFunctions;
      if (nativeFunctions) {
        try {
          const configured = await this.request('Runtime.callFunctionOn', { objectId: probe.result.objectId,
            functionDeclaration: 'function(random,memory){return this.setNative(random,memory)}',
            arguments: nativeFunctions.map(objectId => ({ objectId })), returnByValue: true });
          if (configured.result?.value !== true) failure('input-value-setup-incomplete');
        } catch {
          // The VM's extension creates same-realm native functions; CDP
          // remote object IDs from the main realm must never cross worlds.
          const configured = await this.request('Runtime.callFunctionOn', { objectId: probe.result.objectId,
            functionDeclaration: 'function(){return this.hasNative()}', returnByValue: true });
          if (configured.result?.value !== true) failure('input-value-setup-incomplete');
        }
      }
      const armed = await this.request('Runtime.callFunctionOn', { objectId: probe.result.objectId,
        functionDeclaration: 'function(){return this.arm()}', returnByValue: true });
      if (armed.result?.value !== true) failure('input-value-setup-incomplete');
      installed++;
    } finally { this.probing--; }
  }
  async initialize() {
    await this.request('Runtime.enable');
    await this.request('Debugger.enable');
    if (nativeStatePath) {
      try {
        const native = await this.request('Runtime.evaluate', {
          expression: `process.getBuiltinModule('module').createRequire(${JSON.stringify(__filename)})(${JSON.stringify(nativeStatePath)})`,
          silent: true,
        });
        if (!native.result?.objectId || native.exceptionDetails) throw new Error('state reader unavailable');
        const properties = await this.request('Runtime.getProperties', { objectId: native.result.objectId, ownProperties: true });
        this.nativeFunctions = ['randomState','memoryState'].map(name => properties.result.find(item => item.name === name)?.value?.objectId);
        if (this.nativeFunctions.some(value => !value)) throw new Error('state reader unavailable');
      } catch { failure('input-value-setup-incomplete'); }
    }
    await this.request('NodeRuntime.notifyWhenWaitingForDisconnect', { enabled: true });
    if (!this.parent) await this.request('NodeWorker.enable', { waitForDebuggerOnStart: true });
    // Known VM creation APIs pause before a new realm can execute. Ordinary
    // modules need no per-script pause after this context's functions are bound.
    for (const id of this.contextIds) await this.install(id);
    this.ready = true;
    if (this.initialPause) this.onPause(this.initialPause);
  }
  onPause(params) {
    if (!this.ready) { this.initialPause = params; return; }
    const top = params.callFrames?.[0];
    const valueBreakpoint = (params.hitBreakpoints || []).find(id => this.valueBreakpoints.has(id));
    if (valueBreakpoint) {
      this.request('Debugger.evaluateOnCallFrame', {
        callFrameId: top.callFrameId, expression: '[events,count,loss,intact]', silent: true, returnByValue: true,
      }).then(result => consumeValues(this, this.valueBreakpoints.get(valueBreakpoint), result.result?.value))
        .catch(() => failure('input-values-unavailable'))
        .finally(() => this.request('Debugger.resume').catch(() => {}));
      return;
    }
    const script = top && this.scripts.get(top.location.scriptId);
    if (this.probing && script?.url === sourceFile) { this.request('Debugger.resume').catch(() => {}); return; }
    for (const id of params.hitBreakpoints || []) {
      const category = this.breakpoints.get(id);
      if (categories.includes(category) && !this.probing) counts[category] = 1;
    }
    // Serialize new-realm setup while leaving protocol responses unblocked.
    this.queue = this.queue.then(async () => {
      if ((params.hitBreakpoints || []).some(id => this.breakpoints.get(id) === 'realm') && !this.realmBreakpoint) {
        const value = await this.request('Debugger.setInstrumentationBreakpoint', { instrumentation: 'beforeScriptExecution' });
        this.realmBreakpoint = value.breakpointId;
      }
      if (script?.executionContextId) await this.install(script.executionContextId);
      if (params.reason === 'instrumentation' && this.realmBreakpoint &&
          [...this.contextIds].every(id => this.installedContexts.has(id))) {
        await this.request('Debugger.removeBreakpoint', { breakpointId: this.realmBreakpoint });
        this.realmBreakpoint = null;
      }
      await Promise.all([...sessions].flatMap(session => [...session.breakpoints].filter(([, category]) => counts[category]).map(async ([id]) => {
        session.breakpoints.delete(id);
        try { await session.request('Debugger.removeBreakpoint', { breakpointId: id }); } catch {}
      })));
      if (this.parent && !this.ready) failure('worker-start-unobserved');
      await this.request('Debugger.resume');
    }).catch(() => { failure('context-setup-failed'); this.release(); });
  }
  finish() {
    if (this.finished) return;
    this.finished = true; completed++;
    if (!this.ready) failure('session-setup-incomplete');
  }
  async finishValues() {
    // At NodeRuntime.waitingForDisconnect the isolate no longer admits normal
    // JS calls. The private exit probes must have flushed before that boundary.
    if ([...this.valueProbes.keys()].some(id => !this.valueClosed.has(id))) failure('input-values-unavailable');
  }
  async release() {
    if (this.released) return;
    this.released = true;
    try { await this.request('Runtime.runIfWaitingForDebugger'); } catch {}
    try { await this.request('Debugger.disable'); } catch {}
    if (this.parent) {
      this.parent.request('NodeWorker.detach', { sessionId: this.workerId }).catch(() => {});
    } else { try { this.socket.close(); } catch {} }
  }
}

async function endpoint(value) {
  if (!value || !Number.isSafeInteger(value.pid) || value.pid <= 0 ||
      typeof value.url !== 'string' || !/^ws:\/\/127\.0\.0\.1:\d+\/[0-9a-f-]{36}$/.test(value.url)) {
    failure('invalid-endpoint'); return;
  }
  if (endpoints.has(value.url)) { failure('duplicate-endpoint'); return; }
  if (endpoints.size >= MAX_SESSIONS) { failure('session-limit'); return; }
  endpoints.add(value.url);
  const socket = new WebSocket(value.url); sockets.add(socket);
  const session = new Session(message => socket.send(message), socket);
  socket.addEventListener('message', event => {
    if (typeof event.data !== 'string' || event.data.length > 1024 * 1024) { failure('invalid-protocol-message'); return; }
    try { session.message(JSON.parse(event.data)); } catch { failure('invalid-protocol-message'); }
  });
  socket.addEventListener('close', () => {
    sockets.delete(socket);
    if (!session.finished && !stopping) failure('session-disconnected');
  });
  socket.addEventListener('error', () => { failure('transport-failed'); });
  socket.addEventListener('open', async () => {
    try {
      const identity = await session.request('Runtime.evaluate', {
        expression: '({pid:process.pid,version:process.version})', returnByValue: true,
      });
      if (identity.result?.value?.pid !== value.pid || identity.result?.value?.version !== process.version)
        throw new Error('identity-mismatch');
      processIds.add(value.pid);
      await session.initialize();
      fs.writeFileSync(path.join(directory, `ready-${value.pid}`), '', { mode: 0o600 });
    } catch { failure('session-setup-failed'); session.release(); }
  });
}

let pendingInput = '';
const input = fs.createReadStream(path.join(directory, 'endpoints.pipe'), { encoding: 'utf8' });
input.on('data', chunk => {
  pendingInput += chunk;
  if (pendingInput.length > 65536) { failure('endpoint-limit'); pendingInput = ''; return; }
  let newline;
  while ((newline = pendingInput.indexOf('\n')) >= 0) {
    const row = pendingInput.slice(0, newline); pendingInput = pendingInput.slice(newline + 1);
    try { endpoint(JSON.parse(row)).catch(() => failure('session-setup-failed')); }
    catch { failure('invalid-endpoint'); }
  }
});
input.on('error', () => failure('endpoint-channel-failed'));
process.stdin.resume();
async function stop() {
  if (stopping) return;
  stopping = true;
  if (pendingInput.trim()) failure('endpoint-truncated');
  if (!sessions.size) failure('no-runtime-session');
  if ([...sessions].some(session => !session.finished)) failure('session-completion-unobserved');
  await Promise.allSettled([...sessions].map(session => session.release()));
  input.destroy();
  sendLine({ kind: 'result', counts, sessions: sessions.size, contexts, installed, completed,
    workers: workerCount, process_ids: [...processIds].sort((a,b) => a-b), values: valueRecords,
    reasons: [...reasons].sort() });
  process.exit(0);
}
process.stdin.on('data', stop);
process.stdin.on('end', stop);
process.on('SIGTERM', stop);
sendLine({ kind: 'ready' });

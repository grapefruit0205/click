// Evaluated by the external controller before project code in each realm.
// The private probe is retained by its V8 object id, never exported to the
// target. Values come from the one original call; no input is sampled twice.
function createValueProbe() {
  const apply = Reflect.apply;
  const create = Object.create;
  const define = Object.defineProperty;
  const descriptor = Object.getOwnPropertyDescriptor;
  const bigintString = BigInt.prototype.toString;
  const numberString = Number.prototype.toString;
  const same = Object.is;
  const weakGet = WeakMap.prototype.get;
  const weakSet = WeakMap.prototype.set;
  const buffers = new WeakMap();
  const bufferGetter = descriptor(Object.getPrototypeOf(Int32Array.prototype), 'buffer').get;
  const offsetGetter = descriptor(Object.getPrototypeOf(Int32Array.prototype), 'byteOffset').get;
  const lengthGetter = descriptor(SharedArrayBuffer.prototype, 'byteLength').get;
  const replacements = [];
  let buffer = create(null), size = 0, total = 0, dropped = 0, sequence = 0;
  let nextBuffer = 0, active = false, flushEach = false;
  let nativeRandom = null, nativeMemory = null;
  const MAX_VALUES = 100000;
  // This function's arguments are read by the external V8 controller while
  // paused. Its reference remains private even after the globals are wrapped.
  function probe(events, count, loss, intact) { return count; }
  function flush(intact = null) {
    if (size || dropped || intact !== null) probe(buffer, size, dropped, intact);
    buffer = create(null); size = 0; dropped = 0;
  }
  function valueRecord(value) {
    const output = create(null);
    output.type = typeof value;
    if (typeof value === 'number') output.value = same(value, -0) ? '-0' : apply(numberString, value, []);
    else if (typeof value === 'bigint') {
      if (value > (1n << 1024n) || value < -(1n << 1024n)) return null;
      output.value = apply(bigintString, value, []);
    }
    else if (typeof value === 'string') { if (value.length > 1024) return null; output.value = value; }
    else if (typeof value === 'boolean') output.value = value;
    else if (value === undefined) output.value = null;
    else return null;
    return output;
  }
  function record(source, value, memory, state, thrown) {
    if (++total > MAX_VALUES) { dropped++; return; }
    const row = create(null);
    row.source = source; row.sequence = ++sequence; row.value = valueRecord(value);
    row.outcome = thrown ? 'throw' : 'return';
    // Never inspect a thrown object's name/message/stack or call its getters.
    // Its opaque identity is preserved for the caller, with a collection gap.
    if (!row.value) { row.value = create(null); row.value.type = 'opaque'; row.value.value = null; }
    row.memory = memory;
    row.state = state;
    buffer[size++] = row;
    if (size === 256 || flushEach) flush();
  }
  function replace(object, name, source, shared) {
    const previous = descriptor(object, name);
    if (!previous || typeof previous.value !== 'function' || !previous.configurable)
      return false;
    const original = previous.value;
    // Method syntax preserves non-constructibility (e.g. new Math.random()
    // must still throw rather than allocate an object).
    const wrapper = { call(...args) {
      let before = null, backing = null;
      try {
        if (source === 'math-random' && nativeRandom) before = nativeRandom(original);
        if (shared && nativeMemory) { backing = apply(bufferGetter, args[0], []); before = nativeMemory(backing); }
      } catch {}
      let result, thrown = false;
      try { result = apply(original, this, args); }
      catch (error) { result = error; thrown = true; }
      let after = null;
      try {
        if (source === 'math-random' && nativeRandom) after = nativeRandom(original);
        if (shared && backing && nativeMemory) after = nativeMemory(backing);
      } catch {}
      let memory = null;
      if (shared) {
        try {
          const backing = apply(bufferGetter, args[0], []);
          let identity = apply(weakGet, buffers, [backing]);
          if (!identity) { identity = ++nextBuffer; apply(weakSet, buffers, [backing, identity]); }
          memory = create(null);
          memory.buffer = identity;
          memory.byteLength = apply(lengthGetter, backing, []);
          memory.byteOffset = apply(offsetGetter, args[0], []);
          // Do not repeat a user valueOf/toPrimitive conversion. Unsupported
          // index objects remain an explicit gap in the consumed-state model.
          memory.index_argument = typeof args[1] === 'number' ? valueRecord(args[1]) : null;
          memory.arguments = args.length <= 9 ? create(null) : null;
          if (memory.arguments) for (let index = 1; index < args.length; index++)
              memory.arguments[index - 1] = valueRecord(args[index]);
        } catch { memory = null; }
      }
      const state = create(null);
      state.before = typeof before === 'string' ? before : null;
      state.after = typeof after === 'string' ? after : null;
      record(source, result, memory, state, thrown);
      if (thrown) throw result;
      return result;
    } }.call;
    define(wrapper, 'name', { value: original.name, configurable: true });
    define(wrapper, 'length', { value: original.length, configurable: true });
    define(object, name, { ...previous, value: wrapper });
    replacements[replacements.length] = { object, name, previous, wrapper };
    return true;
  }
  function arm() {
    if (active) return false;
    active = true;
    let complete = replace(Date, 'now', 'date-now', false);
    complete = replace(Math, 'random', 'math-random', false) && complete;
    for (const name of ['add','and','compareExchange','exchange','load','notify','or','store','sub','wait','xor'])
      complete = replace(Atomics, name, 'atomics-' + name, true) && complete;
    if (typeof process === 'object' && typeof process.on === 'function') {
      const closing = () => { flushEach = true; finish(); };
      process.on('beforeExit', closing);
      process.on('exit', closing);
    } else {
      // A plain VM realm has no Node lifecycle to flush a final partial batch.
      flushEach = true;
    }
    return complete;
  }
  function finish() {
    let intact = true;
    for (let index = 0; index < replacements.length; index++) {
      const item = replacements[index];
      const current = descriptor(item.object, item.name);
      if (current?.value !== item.wrapper) intact = false;
    }
    flush(intact);
    return { intact, total, sequence };
  }
  function restore() {
    for (let index = 0; index < replacements.length; index++) {
      const item = replacements[index];
      if (descriptor(item.object, item.name)?.value === item.wrapper)
        define(item.object, item.name, item.previous);
    }
  }
  function setNative(random, memory) {
    if (active || typeof random !== 'function' || typeof memory !== 'function') return false;
    nativeRandom = random; nativeMemory = memory; return true;
  }
  function hasNative() { return !!(nativeRandom && nativeMemory); }
  return { probe, arm, finish, restore, setNative, hasNative };
}

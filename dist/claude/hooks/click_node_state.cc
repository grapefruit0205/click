#include <node.h>
#include <v8.h>
#include <v8-internal.h>
#include <v8-extension.h>
#include <array>
#include <atomic>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <unordered_map>

namespace click_probe {
using v8::FunctionCallbackInfo;
using v8::Value;
using I = v8::internal::Internals;
using Address = v8::internal::Address;

// SHA-256 (FIPS 180-4), kept in this file so the companion depends on nothing
// the host process must export: Node links OpenSSL statically and the
// Windows build does not expose it to addons.
constexpr size_t kDigestLength = 32;
struct Sha256 {
  static constexpr uint32_t kRound[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
  static uint32_t Rotate(uint32_t value, unsigned bits) { return (value >> bits) | (value << (32 - bits)); }
  static void Block(uint32_t state[8], const unsigned char* block) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++)
      w[i] = (uint32_t(block[i*4]) << 24) | (uint32_t(block[i*4+1]) << 16) | (uint32_t(block[i*4+2]) << 8) | uint32_t(block[i*4+3]);
    for (int i = 16; i < 64; i++) {
      const uint32_t s0 = Rotate(w[i-15], 7) ^ Rotate(w[i-15], 18) ^ (w[i-15] >> 3);
      const uint32_t s1 = Rotate(w[i-2], 17) ^ Rotate(w[i-2], 19) ^ (w[i-2] >> 10);
      w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    uint32_t a = state[0], b = state[1], c = state[2], d = state[3], e = state[4], f = state[5], g = state[6], h = state[7];
    for (int i = 0; i < 64; i++) {
      const uint32_t t1 = h + (Rotate(e, 6) ^ Rotate(e, 11) ^ Rotate(e, 25)) + ((e & f) ^ (~e & g)) + kRound[i] + w[i];
      const uint32_t t2 = (Rotate(a, 2) ^ Rotate(a, 13) ^ Rotate(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
      h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d; state[4] += e; state[5] += f; state[6] += g; state[7] += h;
  }
  static void Digest(const unsigned char* data, size_t length, unsigned char* out) {
    uint32_t state[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    size_t offset = 0;
    for (; offset + 64 <= length; offset += 64) Block(state, data + offset);
    unsigned char tail[128];
    const size_t remaining = length - offset;
    std::memcpy(tail, data + offset, remaining);
    tail[remaining] = 0x80;
    const size_t padded = remaining < 56 ? 64 : 128;
    std::memset(tail + remaining + 1, 0, padded - remaining - 1);
    const uint64_t bits = uint64_t(length) * 8;
    for (int i = 0; i < 8; i++) tail[padded - 1 - i] = static_cast<unsigned char>(bits >> (8 * i));
    Block(state, tail);
    if (padded == 128) Block(state, tail + 64);
    for (int i = 0; i < 8; i++) {
      out[i*4] = static_cast<unsigned char>(state[i] >> 24); out[i*4+1] = static_cast<unsigned char>(state[i] >> 16);
      out[i*4+2] = static_cast<unsigned char>(state[i] >> 8); out[i*4+3] = static_cast<unsigned char>(state[i]);
    }
  }
};
void State(const FunctionCallbackInfo<Value>& args) {
  auto isolate = args.GetIsolate();
  if (args.Length() != 1 || !args[0]->IsFunction()) return;
  auto context = args[0].As<v8::Function>()->GetCreationContextChecked();
  Address raw = v8::internal::ValueHelper::ValueAsAddress(*context);
  if (sizeof(Address) != 8 || v8::internal::kApiTaggedSize != 8 || !I::HasHeapObjectTag(raw)) return;
  // Exact Node 22.23.2 / V8 12.4 native-context slot layout, without pointer
  // compression. Copy while no V8 API allocations can move the heap objects.
  const auto index = I::SmiValue(I::ReadTaggedSignedField(raw, 1280));
  const auto state = I::ReadTaggedPointerField(raw, 1288);
  const auto cache = I::ReadTaggedPointerField(raw, 1296);
  if (index < 0 || index > 64 || !I::HasHeapObjectTag(state) || !I::HasHeapObjectTag(cache)) return;
  const auto state_size = I::SmiValue(I::ReadTaggedSignedField(state, 8));
  const auto cache_size = I::SmiValue(I::ReadTaggedSignedField(cache, 8));
  if (state_size != 16 || cache_size != 64) return;
  const uint64_t first = I::ReadRawField<uint64_t>(state, 16);
  const uint64_t second = I::ReadRawField<uint64_t>(state, 24);
  std::array<unsigned char, 512> contents;
  std::memcpy(contents.data(), reinterpret_cast<void*>(cache - 1 + 16), contents.size());
  std::array<unsigned char, kDigestLength> digest;
  Sha256::Digest(contents.data(), contents.size(), digest.data());
  char encoded[65];
  for (size_t i = 0; i < digest.size(); i++) std::snprintf(encoded + i*2, 3, "%02x", digest[i]);
  char output[160];
  std::snprintf(output, sizeof(output), "v8-xorshift128-cache64-v1:%d:%016" PRIx64 ":%016" PRIx64 ":%s",
                index, first, second, encoded);
  args.GetReturnValue().Set(v8::String::NewFromUtf8(isolate, output).ToLocalChecked());
}
struct MemoryIdentity {
  std::weak_ptr<v8::BackingStore> backing;
  uint64_t id;
};
std::mutex memory_mutex;
std::unordered_map<void*, MemoryIdentity> memory_ids;
uint64_t next_memory_id = 0;
void MemoryState(const FunctionCallbackInfo<Value>& args) {
  if (args.Length() != 1 || !args[0]->IsSharedArrayBuffer()) return;
  auto backing = args[0].As<v8::SharedArrayBuffer>()->GetBackingStore();
  const size_t size = backing->ByteLength();
  if (size > 65536 || !backing->Data()) return;
  uint64_t identity;
  {
    std::lock_guard<std::mutex> lock(memory_mutex);
    auto found = memory_ids.find(backing->Data());
    if (found == memory_ids.end() || found->second.backing.expired()) {
      if (memory_ids.size() >= 4096) return;
      identity = ++next_memory_id;
      memory_ids[backing->Data()] = {backing, identity};
    } else {
      identity = found->second.id;
    }
  }
  // A byte sample is deliberately not claimed to be an atomic whole-buffer
  // snapshot. Other workers may write while it is read; full ordering is a
  // separate requirement. Atomic byte loads avoid a torn individual byte.
  std::array<unsigned char, 65536> contents;
  auto data = static_cast<unsigned char*>(backing->Data());
  for (size_t index = 0; index < size; index++)
    contents[index] = std::atomic_ref<unsigned char>(data[index]).load(std::memory_order_relaxed);
  std::array<unsigned char, kDigestLength> digest;
  Sha256::Digest(contents.data(), size, digest.data());
  char encoded[65];
  for (size_t i = 0; i < digest.size(); i++) std::snprintf(encoded + i*2, 3, "%02x", digest[i]);
  char output[128];
  std::snprintf(output, sizeof(output), "shared-bytes-sample-v1:%" PRIu64 ":%zu:%s", identity, size, encoded);
  args.GetReturnValue().Set(v8::String::NewFromUtf8(args.GetIsolate(), output).ToLocalChecked());
}
// V8 extensions instantiate native functions in the newly created realm. CDP
// correctly refuses to pass a main-realm remote object to a VM realm. This
// avoids bypassing that boundary with a VM escape or exposing Node's process.
// The controller consumes and deletes this bootstrap-only binding before the
// first project script in the realm. It is never a completion/authority input.
class StateExtension : public v8::Extension {
 public:
  StateExtension() : v8::Extension("click/state-v1", R"JS(
    (function() {
      native function clickRandomState();
      native function clickMemoryState();
      Object.defineProperty(globalThis, '__click_native_state_bridge_v1', {
        value: {randomState: clickRandomState, memoryState: clickMemoryState},
        configurable: true
      });
    })();
  )JS") { set_auto_enable(true); }
  v8::Local<v8::FunctionTemplate> GetNativeFunctionTemplate(
      v8::Isolate* isolate, v8::Local<v8::String> name) override {
    v8::String::Utf8Value text(isolate, name);
    return v8::FunctionTemplate::New(isolate,
        std::strcmp(*text, "clickRandomState") == 0 ? State : MemoryState);
  }
};
std::once_flag extension_registered;
NODE_MODULE_INIT() {
  std::call_once(extension_registered, [] {
    v8::RegisterExtension(std::make_unique<StateExtension>());
  });
  NODE_SET_METHOD(exports, "randomState", State);
  NODE_SET_METHOD(exports, "memoryState", MemoryState);
}
}

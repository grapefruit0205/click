#include <node.h>
#include <v8.h>
#include <v8-internal.h>
#include <v8-extension.h>
#include <openssl/sha.h>
#include <array>
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
  std::array<unsigned char, SHA256_DIGEST_LENGTH> digest;
  SHA256(contents.data(), contents.size(), digest.data());
  char encoded[65];
  for (size_t i = 0; i < digest.size(); i++) std::snprintf(encoded + i*2, 3, "%02x", digest[i]);
  char output[160];
  std::snprintf(output, sizeof(output), "v8-xorshift128-cache64-v1:%d:%016lx:%016lx:%s", index, first, second, encoded);
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
    contents[index] = __atomic_load_n(data + index, __ATOMIC_RELAXED);
  std::array<unsigned char, SHA256_DIGEST_LENGTH> digest;
  SHA256(contents.data(), size, digest.data());
  char encoded[65];
  for (size_t i = 0; i < digest.size(); i++) std::snprintf(encoded + i*2, 3, "%02x", digest[i]);
  char output[128];
  std::snprintf(output, sizeof(output), "shared-bytes-sample-v1:%lu:%zu:%s", identity, size, encoded);
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

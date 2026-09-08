# Runtime identity and offline execution contract

Phase 3 binds an execute-level adapter to the selected launcher, its runtime,
and bounded project configuration. Runtime identity is collected at the same
prepare, claim, collection, and result boundaries as executable identity. It
never imports a project, starts a validator, installs a package, or returns a
path or file content in a public receipt.

The collector hashes local content within fixed limits. Node profiles include
the Node runtime, package manifest, lock files, installed npm lock state, and
JavaScript or TypeScript configuration. Go, Cargo, JVM, .NET, and native build
profiles include their project and toolchain configuration. A changed binding
invalidates exact reuse. An incomplete binding permits an authorized real run
but cannot create reusable evidence. An unsafe binding is denied before the
runner is issued.

The following network-capable launchers require an explicit offline boundary:

- Go requires `GOTOOLCHAIN=local` and `GOPROXY=off`.
- Cargo requires `--offline` or `CARGO_NET_OFFLINE=true`; a project
  `rust-toolchain` override remains blocked because the installed toolchain
  cannot yet be proven without invoking rustup.
- Direct Gradle requires `--offline`; direct Maven requires `-o` or
  `--offline`.
- Gradle and Maven wrappers remain blocked because their distribution cache is
  not yet bound.
- .NET requires `--no-restore`.
- `npx`, `pnpx`, and `bunx` require `--no-install`, an already present local
  `node_modules/.bin` target, and a package manifest whose declared bin points
  to that launcher. Their installed dependency tree is content-bound within
  the runtime limits. Derived `.cache`, `.vite`, and `.vitest` roots are
  excluded so runner-created result caches cannot invalidate an otherwise
  unchanged receipt.

These requirements prevent Click from silently turning verification into a
dependency or runtime installation. They do not claim that project-defined
test or lifecycle code is side-effect-free. Such code still runs only through
the existing direct-argv authorization and process boundary.

Runtime/config files are limited to 64 records, 8 MiB per configuration file,
512 MiB per runtime executable or installed package tree, and 50,000 installed
tree entries. Missing, oversized, externally linked, non-file, or racing inputs
make the identity incomplete. File content and set membership are
authoritative; mtime is not.

Phase 3 locally proves Node `--test`, Node `--check`, npm `test`, and Go
`test`. Cargo, JVM, .NET, TypeScript, CMake, and CTest remain unverified on the
local host because their toolchains are absent. Static recognition is not a
support claim, and automatic discovery or sharding for these adapters is a
later phase.

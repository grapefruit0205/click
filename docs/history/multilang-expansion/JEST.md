# Jest 30 automatic sharding profile

Phase 6 supports one bounded, offline command profile:

```text
npx --no-install jest --runInBand [--runTestsByPath <exact test file>]
```

The fixture pins the npm package `jest: "30.5.1"`; its official CLI reports
`30.5.0`. Both values are checked. Provisioning uses the committed npm
lockfile. Inventory, proposal, baseline verification, and ordinary execution
never download a package.

Click uses Jest's `--listTests --json` output as file-level candidate
inventory. Child commands always use `--runTestsByPath`, so duplicate basenames
and paths containing regular-expression metacharacters remain exact. Listing
tests does not produce PASS evidence. `--findRelatedTests` and `--onlyChanged`
are not reuse authority.

The supported fixture is CommonJS with an optional pinned Babel transform for
TypeScript. A static `package.json` Jest object may use `rootDir: "."`, the
Node environment, the exact pinned TypeScript transform, setup files, global
setup, and global teardown. Dynamic Jest config files, custom discovery
patterns or regexes, multi-project layouts, ESM mode, custom environments,
sequencers, runners, watch modes, snapshot updates, and arbitrary filters keep
the whole parent command.

Configuration identity includes package and lock state, Node and npx identity,
the installed dependency tree, the Jest launcher, TypeScript/Babel identity
files, referenced setup files, and repository snapshot files. Test membership,
snapshot, configuration, runtime, or adapter changes invalidate generated
setup state. Candidate dependencies remain `**`, so this phase claims safe
same-revision decomposition and exact receipt reuse, not selective
cross-revision JavaScript reuse or native observation.

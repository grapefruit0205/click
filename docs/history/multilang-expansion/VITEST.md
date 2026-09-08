# Vitest 5 automatic sharding profile

Phase 5 supports one deliberately narrow, one-shot command:

```text
npx --no-install vitest run [optional exact test file]
```

The project must declare exactly `vitest: "5.0.0"`, carry an npm lockfile that
resolves the same installed version, and already contain the locked
`node_modules` installation. Provisioning may use `npm ci`; inventory,
proposal, baseline, normal verification, and reuse never download packages.

Click calls Vitest's documented `list --json --run` command to collect tests.
The result is candidate inventory. Click normalizes every ID as its
repository-relative file, Vitest full test name, and deterministic occurrence
number. It accepts an exact-file child only when collection returns tests from
that file and no other file. This extra check matters because Vitest documents
CLI file filters as substring filters rather than exact path selectors.

The initial profile uses Vitest's default discovery only. It rejects Vitest or
Vite config files, workspace files, the `package.json` `vitest` field, direct
`vitest`, package scripts, browser mode, watch mode, update mode, coverage,
projects, pools, custom reporters, and additional CLI options. Those shapes
fall back to the original parent verification instead of producing a split.

## Identity and invalidation

Inventory binds the npx launcher, Node executable, Vitest adapter source,
package and installed lock state, Vitest manifest/entry file, TypeScript or
JavaScript project identity files, and their content digests. Normal Click
verification additionally binds the local launcher and the bounded installed
dependency tree. Runner-created `node_modules/.vite` result data is treated as
a derived cache. Setup cost and baseline probes add `--no-cache` so those
probes remain read-only.

Any test membership change, test-file content change, supported identity-file
change, adapter change, npx or Node runtime change, unstable collection,
mutation during collection, output/timeout limit, or selector mismatch makes
the generated setup stale or unsupported. `status` reports the reason and
`refresh` regenerates the proposal. A committed policy whose current parent
discovery is wider than its inventory returns the whole parent command.

Dependency candidates are conservative in this first profile: every Vitest
file shard depends on `**`. This permits same-revision decomposition and exact
receipt reuse without claiming selective cross-revision reuse. A later phase
may narrow this only with separately tested JavaScript/TypeScript dependency
evidence.

References: [Vitest CLI](https://vitest.dev/guide/cli) and
[Vitest advanced API](https://vitest.dev/api/advanced/vitest).

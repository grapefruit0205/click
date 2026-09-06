# Click Product Constitution

Status: **Canonical**

Effective: **2026-09-01**

> **Click returns revision-aware evidence for host-authorized AI execution and,
> when Guarded is selected, binds higher-risk execution to approved intent.**
>
> **Click은 host가 승인한 AI 실행의 revision-aware evidence를 돌려주며,
> Guarded를 선택하면 고위험 실행을 사용자가 승인한 의도에 결속한다.**

This document defines the stable product boundary for Click. Runtime behavior,
documentation, tests, host adapters, and future features must remain consistent
with it.

## Core purpose

Click is a mode-aware execution-integrity and evidence runtime. Its common
integrity layer records revision-bound evidence, exact runner claims, and honest
authority metadata. In **Evidence**, the host remains the execution authority
and Click records intent and follow-up lineage without creating or approving a
contract. In **Guarded**, Click additionally binds a canonical statement of
intent to later user approval and mediates observable mutations and external
side effects under that approved authority. When Click directly runs argv
verification in either mode, it records a stronger receipt for the protected
workspace, environment, executable, and known host coverage that produced the
result.

Click Core does not decide how a capable model should explore, plan, or choose
the best implementation or verification strategy. It supplies guarantees that
remain necessary even if the model is perfect.

## Core admission test

A feature may enter Click Core only when the answer to **all three** questions
is yes:

1. Would an external guarantee still be necessary if the model were perfect?
2. Can the runtime observe the relevant action or result?
3. Would failure threaten authority, side-effect control, or evidence integrity?

If any answer is no, the feature does not belong in Core. Hidden reasoning,
semantic guesses, and model-quality assumptions are not runtime observations.

## Policy layers

### CORE

Cross-model, cross-host invariants that may fail closed because they protect
authority, side effects, or evidence integrity. Core behavior must be grounded
in an observable event or result and covered by deterministic tests.

### USER_POLICY

Optional restrictions or budgets that the user explicitly selects or approves.
Core may enforce an approved policy value exactly, but selecting the value is
not itself a Core judgment. A user policy must not silently become a universal
product invariant.

### HEURISTIC

Advice, telemetry, or experimental strategy intended to improve model workflow.
Heuristics must not grant authority, manufacture evidence, mark work complete,
or hard-block otherwise authorized execution. They may be tuned per model or
host without changing Click's constitutional guarantees.

## Core guarantees

Click Core owns these common integrity guarantees:

- stating the selected authority mode accurately and never manufacturing an
  approval that did not occur;
- binding one-use runners to the exact action, request, runtime state, and
  mutation revision;
- preventing cancellation bypass, replay, token substitution, and state tampering;
- binding every evidence ledger entry to the active intent or contract identity
  and mutation revision;
- binding argv verification receipts additionally to the protected workspace
  state, execution environment, executable identity, and known host coverage;
- invalidating stale evidence when the protected implementation changes;
- recovering outstanding runner state without weakening exact binding,
  expiry, cancellation, or replay checks;
- preserving an append-only capability-claim ledger for the active session and
  auditable current authority state and evidence receipts;
- exporting a deterministic completion receipt that binds the authority mode,
  observable capability claims, final workspace revision, and evidence lineage;
  and
- adapting Codex, Antigravity, and other hosts onto the same Core protocol.

In **Evidence authority**, Core additionally guarantees:

- `execution_authority: host` and `approval_bound: false`;
- intent and follow-up prompt digest lineage without a canonical approval
  contract; and
- host-observed mutation revision tracking without treating Click as the source
  of mutation permission.

In **Guarded authority**, Core additionally guarantees:

- binding later user approval to the exact canonical contract digest and ID;
- controlling authority for observable mutations and external side effects;
- binding one-use execution capabilities to the approved contract; and
- exporting the contract, staging turn, and approval turn in the completion
  receipt.

Host adapters may normalize host events and output formats. They may not weaken
the Core identity, authorization, runner-binding, or receipt invariants.

Completed Guarded work may carry successful candidates, never approval,
runner tokens, unfinished work or completion, into a separately approved
successor contract. Current check/input/environment/coverage and existing
dependency or precommitted policy bindings must requalify every applied reuse.
Receipt v5 records that lineage without changing Evidence or legacy authority.
Local display summaries, observed control-event telemetry and partial timing
remain HEURISTIC: they neither judge semantic scope nor authorize execution.

## Outside Core

The following are not Click Core responsibilities:

- the model's file-exploration order;
- forced blocking of duplicate reads;
- repository-wide scan counts;
- replan counts or plan-tool usage;
- choosing the optimal verification strategy;
- model-specific workflow tuning; and
- a large context scheduler.

These features may exist as explicit `USER_POLICY`, non-blocking `HEURISTIC`, or
isolated experiments. They must not be presented as universal runtime security
or evidence guarantees.

## Constitutional execution chains

```text
Evidence authority
host-authorized request
    -> intent and follow-up digests
    -> host-observed mutation revision
    -> exact one-use verification request
    -> revision-, tree-, environment-, executable-, and coverage-bound evidence
    -> receipt: approval_bound=false, execution_authority=host

Guarded authority
canonical contract digest and ID
    -> later user approval
    -> exact one-use approved execution request
    -> observable action or side effect
    -> contract- and revision-bound evidence record
    -> stronger tree-, environment-, and executable-bound argv receipt
    -> receipt: approval_bound=true, execution_authority=click-contract
```

The runtime can prove only the observable bindings in the selected chain. It
cannot by itself prove that a follow-up was semantically inside an earlier
scope, that natural-language approval was informed, that a scope matches every
changed line, or that an unmatched manual or external attestation is true. The
current implementation can export an unsigned completion receipt; it
does not yet provide a signed, independently authentic durable history of every
state transition.

## Change rule

Every proposal for a new Core hard gate must document:

1. the observable event or result;
2. the authority, side-effect, or evidence invariant it protects;
3. the concrete failure if it is omitted;
4. why all three Core admission questions are answered yes; and
5. deterministic tests for allow, deny, replay, tampering, cancellation, and
   recovery paths as applicable.

Classification alone does not change shipped behavior. Moving an existing guard
between layers requires a separate behavior change with regression tests and
matching documentation. The current inventory and migration order live in
[GUARD_CLASSIFICATION.md](GUARD_CLASSIFICATION.md).

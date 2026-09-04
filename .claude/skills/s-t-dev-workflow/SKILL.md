---
name: s-t-dev-workflow
description: >
  Guides through SDD to TDD workflow phases: Decision Record (ADR), Requirements
  Definition, Test Design, and TDD Red-Green-Refactor. Auto-triggers during
  feature development or bug fixes when SDD-TDD rules are configured in the
  project. Also triggers when the user mentions writing specs, designing tests,
  or starting a TDD cycle.
  Do NOT use for initial s-t-dev setup (use /s-t-dev instead). Do NOT use for
  behavior-preserving refactoring (use hook-refactor-with-test instead).
user-invocable: false
---

# SDD → TDD Workflow Guide

## Phase Detection

Determine the starting phase from current state:

- A design decision is needed and is not recorded under `docs/permanent/adr/`
  → Phase 0 (Decision Record)
- No spec for the current feature at `docs/work/<task-name>/spec.md` (or wherever
  the repo's convention puts it) → Phase 1 (Requirements Definition)
- Spec exists, no tests → Phase 2 (Test Design)
- Tests exist, failing → Phase 3 (TDD Implementation)
- Tests exist, all passing → Feature complete or new feature needed

## Phase 0: Decision Record (ADR)

**Not a mandatory phase.** Enter it only when the work involves a design decision
with real alternatives that someone may later question. Skip it when the change
merely implements something already settled.

1. Delegate to the `adr-capture` skill to record the decision at
   `docs/permanent/adr/NNNN-slug.md` (extraction steps and format live there; do not
   duplicate them here)
2. Move to Phase 1 once the ADR's Status is `accepted`
3. If the decision requires no implementation (operating rules, team structure), stop
   here and do not enter Phase 1

## Phase 1: Requirements Definition

1. Ask the user what they want to build (feature, fix, change)
2. Name the work folder after the task or issue (e.g. `docs/work/oauth-login/`,
   `docs/work/issue-142/`) and create `spec.md` in it. Name it after the task,
   not the branch — branches get renamed and deleted
3. Add a `README.md` in the same work folder with Status
   (in progress / blocked / done), Branch, and Issue
4. The spec (`spec.md`) should contain:
   - A link to the underlying ADR when Phase 0 was used
     (`Decision: [NNNN. Title](../../permanent/adr/NNNN-slug.md)`)
   - Purpose and scope
   - Acceptance criteria (in verifiable form)
   - Edge cases and error cases
   - Impact scope on existing code
5. Get user confirmation on the spec

## Phase 2: Test Design

1. Read the spec document
2. Propose test cases:
   - Happy path (at least one per acceptance criterion)
   - Boundary values and edge cases
   - Error cases
3. Get user approval on the test plan
4. Create test files (all tests must be in Red state)

> Scope: this phase targets **new features and bug fixes**. Behavior-preserving
> refactoring keeps tests green by principle and is therefore out of scope —
> delegate it to hook-refactor-with-test.

## Phase 3: TDD Implementation (Red-Green-Refactor)

This phase contains the full Red-Green-Refactor cycle. Do NOT invoke
`hook-tdd` — that double-applies TDD instructions, and its `git stash` gate
would stash the spec and tests produced in Phases 1-2.

Tests already exist from Phase 2, so there is no separate test-writing step.
Work one acceptance criterion at a time; complete a cycle before the next.

1. **Red**: Run the tests; confirm the target test FAILS and every pre-existing
   test still PASSES. Do not proceed until failure is confirmed
2. **Green**: Write the minimum code to pass that one test. Never modify the
   test to make it pass. Confirm it PASSES with no regressions
3. **Refactor**: Improve quality while all tests stay green. If no change is
   needed, state why. Run the project's own linter/formatter
4. Repeat 1-3 for the next acceptance criterion
5. Run the full test suite to confirm no regressions
6. Decide the fate of the work folder (`docs/work/<task-name>/`) and propose it
   to the user:
   - **Promote**: move spec content worth keeping to
     `docs/permanent/reference/<feature-name>.md`, and extract design decisions
     as ADRs under `docs/permanent/adr/` (recording may be delegated to
     `adr-capture`). Rewrite for a reader without the original context, and give
     permanent docs a changelog
   - **Discard**: delete the work folder if nothing is worth keeping
   - **Archive**: if unsure, keep it but set Status to `done` in its `README.md`

### Optional agent mode (Python/pytest projects only)

The globally distributed `tdd-implementer` / `tdd-refactorer` agents depend on
pytest and Flake8. Use them only when the stack detected at setup is Python +
pytest, and only for Green/Refactor. Do not use `tdd-test-writer` — test
authoring belongs to Phase 2.

## Prohibited

- Writing implementation before the target test exists and is confirmed failing
- Proceeding to Green without a confirmed Red
- Skipping Refactor without an explicit judgement
- `git stash` of the spec or tests created in Phases 1-2
- Invoking `hook-tdd` from within this workflow

## Phase Transition Criteria

| Transition | Condition |
|-----------|-----------|
| Phase 0 → 1 | ADR is `accepted` and the decision requires implementation |
| Phase 1 → 2 | Spec document exists and user approves |
| Phase 2 → 3 | Test files exist and all tests fail as expected |
| Phase 3 done | All tests pass, code quality confirmed, work folder fate decided (promote / discard / archive) |

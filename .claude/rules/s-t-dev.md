# SDD → TDD Development Workflow

This project follows Spec-Driven Development (SDD) → TDD methodology.

## Recommended workflow for code changes

1. **Requirements first**: Before writing code, define requirements, acceptance
   criteria, and edge cases in a spec document
2. **Tests second**: Design and write tests based on the spec. For new features
   and bug fixes, all tests must initially fail (Red state)
3. **Implementation last**: Use TDD Red-Green-Refactor cycle:
   - Red: Confirm tests fail
   - Green: Write minimal code to pass
   - Refactor: Clean up while keeping tests green

## Methodology when planning

When planning a code change, structure the plan along the SDD → TDD flow above
(Spec design → Test design → Implementation plan).

## TDD entry point (single owner)

In this project the TDD entry point is the `s-t-dev-workflow` skill. Do NOT
invoke `hook-tdd` — its Red-Green-Refactor cycle is already contained in
Phase 3 of `s-t-dev-workflow`. Invoking both double-applies TDD instructions.

## Recording decisions (upstream of the spec)

Settle design decisions as an ADR under `docs/permanent/adr/` before starting work.
Write the spec from that ADR and reference it at the top with a `Decision:` line.
Extracting and recording decisions is the `adr-capture` skill's job.

Small changes that carry no decision need no ADR. Decisions that emerge during
implementation are extracted at promotion time (below).

## Spec file location

A spec is a **working document**: place it at
`docs/work/<task-name or issue-NN>/spec.md`. Add a `README.md` in the same work
folder carrying Status / Branch / Issue.

Once implementation is complete, promote whatever is worth keeping to
`docs/permanent/reference/<feature-name>.md`, and extract design decisions as
ADRs under `docs/permanent/adr/`. After promoting, either delete the work folder
or close it by setting its status to `done`.

If the repository already has its own docs convention, follow that instead.

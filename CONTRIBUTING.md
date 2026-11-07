# Contributing to Rankwell

Thanks for helping. This page covers the setup, the checks every change must pass, and how the code is
organised.

## Setup

You need macOS, Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/AkashShahriar55/rankwell.git
```

```bash
cd rankwell && uv sync
```

To try your checkout inside Claude Code, start it with the plugin folder:

```bash
claude --plugin-dir /path/to/rankwell
```

## Checks

Every pull request must pass these; CI runs them on macOS and Ubuntu with Python 3.11 and 3.13.

```bash
uv run ruff check && uv run mypy && uv run pytest -q
```

```bash
claude plugin validate .
```

To run these automatically before every push, enable the repo's hook once:

```bash
git config core.hooksPath .githooks
```

`uv run pytest -m live` calls Apple's real endpoints read-only. It is not part of the normal run and never
writes anything.

## Ground rules

- **Never write to a store from a test.** Tests mock the network with `respx`; see `tests/asc_fake.py` and
  `tests/play_fake.py`. Do not use real keys in tests, fixtures or examples.
- **Every store write is a plan.** Changes to App Store Connect or Google Play go through `rankwell.plans`:
  a `plan_*` function that reads the live state and lists current → new, and an applier that re-checks the
  state hash before writing and calls `plans.audit` for each change.
- **No secrets in output.** Keys and tokens never appear in logs, exceptions, MCP results or plan payloads.
- **Numbers are never invented.** If data is missing, say so and say how to get it.
- **Style.** Ruff with a 120-character line, mypy strict on the package, short comments that say why.

## Where things live

| Folder | Contents |
|---|---|
| `src/rankwell/` | Domain logic, one module per area (`listing.py`, `release.py`, `play.py`, ...) |
| `src/rankwell/stores/` | HTTP clients for the App Store, App Store Connect, Google Play and Google |
| `src/rankwell/mcp/tools_*.py` | MCP tools, loaded automatically |
| `src/rankwell/commands/*.py` | CLI commands, loaded automatically |
| `src/rankwell/db/migrations/` | Numbered SQL migrations; add a new file, never edit an old one |
| `skills/<name>/SKILL.md` | The playbooks Claude follows |
| `knowledge/` | ASO rules cited by id in the skills |
| `docs/` | Architecture and the module contract |

[docs/CONTRACT.md](docs/CONTRACT.md) lists module owners, tool names and conventions.

## How a contribution gets in

1. **Open an issue first** for anything bigger than a small fix, so we agree on the approach before you write
   code. Issues labelled `good first issue` are a good start.
2. **Fork the repository** and work on a branch in your fork.
3. **Open a pull request** against `main`. CI runs once a maintainer approves the workflow run for your first
   contribution.
4. **Review.** The maintainer reviews every pull request; `main` only changes through an approved pull request
   with green checks. Expect questions and requested changes; that is normal.
5. **Merge.** The maintainer merges (usually squashed into one commit).

## Pull requests

- One topic per pull request, with tests for the behaviour it adds or fixes.
- Add a line under `Unreleased` in [CHANGELOG.md](CHANGELOG.md).
- New MCP tools need a docstring that tells Claude what the tool does and when to use it.

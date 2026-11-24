# Contributing to otui

Thank you for helping. otui is for TIMES modellers who do not work in code, so
reports from people using it on real models are as useful as code.

## Reporting a problem

Open an issue with:

- what you did, what you expected, and what happened (a screenshot helps);
- your operating system, `otui --version`, and the output of `otui info`;
- the model, if it is openly licensed. Please don't attach a model you may not
  share. A small openly licensed model that shows the same problem is ideal.
- OTUI Issues listed: https://github.com/esma-ie/otui/issues

If TIMES.jl reads or solves a model differently from GAMS TIMES, otui shows
TIMES.jl's result as it is. Those reports belong to
[TIMES.jl](https://github.com/esma-ie/timesjl/issues). If you're not sure
where a problem belongs, open it here and we will move it.

## Changing the code

Set up as in the README's [Getting started (development)](README.md#getting-started-development),
then work on a branch and open a pull request against `main`.

Before you push:

```sh
uv run ruff check && uv run ruff format --check
uv run pytest -m "not julia"          # fast, no Julia needed
uv run pytest -m julia                # reads and solves demo12 through TIMES.jl
uvx --from "reuse[charset-normalizer]" reuse lint
cd frontend && npm run check && npm run build
```

CI runs all of these on Windows, macOS and Linux, against TIMES.jl's newest
`main` and against the version otui pins.

Some guidelines:

- **Small pull requests**, each with one purpose and with tests for what it
  changes.
- **Native TIMES names.** Tables use TIMES's own set and parameter names and
  headers ([`docs/table-format.md`](docs/table-format.md)). otui does not
  invent its own.
- **TIMES.jl does the TIMES work.** otui reads, compiles and solves models
  only through TIMES.jl, and has no DD parser of its own. If TIMES.jl is
  missing something, the change belongs there.
- **Only openly licensed models** in tests, fixtures and examples. Fixtures
  are downloaded and checked by their SHA-256, never committed.
- **Plain words in the interface.** Users are modellers, not programmers: say
  what happened and what to do next.

## Licence

otui is licensed under AGPL-3.0-or-later. By contributing, you agree that your
contribution is licensed under the same terms. Each file's licence is recorded
in [`REUSE.toml`](REUSE.toml).

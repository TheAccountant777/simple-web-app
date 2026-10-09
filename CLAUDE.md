# Kenya Data Engine (repo: simple-web-app)

**Start here:** `docs/STATUS.md` is the project state, the decisions behind it, and what's next.
`docs/superpowers/specs/2026-10-09-kenya-data-engine-design.md` is the approved spec.

## Dev
- Python ≥3.12 with uv. Package: `src/kenya_data_engine`. CLI: `engine`.
- `uv sync` installs; `make check` runs ruff lint and format check, strict mypy on `src`, and pytest
  with a coverage gate of 85%. It must pass before every commit.
- `uv run pytest tests/<file>::<test> -q --no-cov` runs a focused test.
- Tests never touch the network. Use respx for HTTP and Pydantic AI `FunctionModel`/`TestModel` for
  the LLM (`function_model_returning` in `tests/conftest.py`). For the TUI, use Textual
  `run_test()` pilots.

## Rules
- No number from an LLM: code computes every figure. Claims must be quote-grounded
  (`tools/grounding.py`, exact match).
- Never weaken TLS verification (`tls.py` AIA repair is security-reviewed).
- Secrets must never reach traces, artifacts, LLM captures, the CLI or the TUI. Use
  `Tracer.redact`.
- Stages are typed and isolated, with JSON artifacts in `runs/<id>/`. They fail soft and report
  loudly.
- Sources live in `defaults/sources.yaml` (the user override merges by name). Disable a broken
  source with a note; don't delete it.
- The cloud session has no API keys, and Kenyan sites are proxy-blocked, so live checks run on the
  user's laptop.
- Work on branch `claude/loving-hawking-eucp75`. Don't push `main` without the user's explicit OK.

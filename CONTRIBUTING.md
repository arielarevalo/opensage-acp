# Contributing to opensage-acp

Thanks for your interest in contributing!

## Development setup

```bash
git clone https://github.com/arielarevalo/opensage-acp.git
cd opensage-acp
uv sync
```

## Workflow

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Run the checks:

```bash
uv run ruff check src/ tests/   # Lint
uv run ruff format --check src/ tests/  # Format check
uv run mypy src/                 # Type check
uv run pytest                    # Tests
```

4. Open a pull request against `main`

## Conventions

- **Python** — 3.12+, type hints on all public functions
- **Formatting** — Ruff (runs in CI)
- **Commits** — [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`)
- **Tests** — unit and integration in `tests/`

## Reporting Issues

Use [GitHub Issues](https://github.com/arielarevalo/opensage-acp/issues). Include
steps to reproduce, expected behavior, and actual behavior.

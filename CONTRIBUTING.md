# Contributing to Claw Recall

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/rodbland2021/claw-recall.git
cd claw-recall

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up a test database
python3 setup_db.py

# Copy environment config
cp .env.example .env
# Edit .env with your OpenAI API key (required for semantic search)
```

## Running Tests

```bash
# Run the full test suite
python3 -m pytest test_claw_recall.py -v

# Run a specific test class
python3 -m pytest test_claw_recall.py::TestKeywordSearch -v

# Run with coverage
python3 -m pytest --cov=. --cov-report=term-missing test_claw_recall.py
```

Tests use an in-memory SQLite database — no external services needed.

## Code Style

- **Python 3.10+** — use type hints on public function signatures
- **No bare `except:`** — always catch specific exceptions or `Exception as e`
- **Parameterized SQL** — always use `?` placeholders, never f-strings for user input
- **Relative imports** — the codebase uses direct imports (`from search import ...`)
- Keep functions focused. If a function exceeds ~50 lines, consider splitting it.

## Security Guidelines

This is a tool that indexes private conversation data. Security matters:

- **Never log or return** conversation content in error messages
- **Validate all paths** — use `Path.resolve()` and `relative_to()` for path checks
- **Parameterize all SQL** — no string interpolation in queries
- **Escape shell arguments** — use `shlex.quote()` for subprocess commands
- **No hardcoded credentials** — use environment variables or config files
- **No PII in examples** — use fictitious names in tests, docs, and comments

## Pull Request Process

1. **Fork the repo** and create a feature branch from `master`
2. **Write tests** for new functionality
3. **Run the full test suite** — all tests must pass
4. **Keep commits focused** — one logical change per commit
5. **Write clear commit messages** — explain the "why", not just the "what"
6. **Open a PR** with a description of what changed and why

## Adding a New Data Source

To add a new data source (like a new messaging platform):

1. Add the polling function in `capture_sources.py` following the `poll_gmail()` pattern
2. Add noise filtering (see `_is_gmail_noise()` for examples)
3. Add a test class in `test_claw_recall.py`
4. Document any new environment variables in `.env.example`
5. Update the README with setup instructions

## Reporting Issues

- Use [GitHub Issues](https://github.com/rodbland2021/claw-recall/issues) for bugs and feature requests
- Include steps to reproduce for bugs
- Include your Python version and OS

## Community

- [Discord](https://discord.gg/ZhCe3t8kFD) — join the discussion

# Contributing

Keep changes focused and explain their effect on restoration behavior. Include a small deterministic test when changing a numerical contract or public interface. This is a code-only release: do not attach research datasets, trained weights, paper figures, result tables, manuscript highlights, or experiment outputs to commits or pull requests. Use invented values in examples and tests; keep research findings out of public documentation.

Install the development dependencies with `python -m pip install -e ".[dev]"`, then run:

```sh
python -m ruff check src scripts tests
python -m ruff format --check src scripts tests
python scripts/validate_installation.py
python scripts/check_release_contents.py
python -m pytest
```

Document changes to numerical constants, source-based valuation parameters, candidate selection, seeds, resource budgets, and checkpoint selection explicitly. These require scientific review. Keep synthetic fixture checks separate from research-scale validation.

The release-content check inspects tracked file paths, including staged additions, and rejects result/artifact directories, rendered figures, and unapproved tabular files. It also runs in CI. It cannot identify all study findings embedded in prose or source literals, so review `git diff --cached` before pushing. Keep local research output in ignored directories and never force-add it.

Preserve the provenance ledger when moving or changing copied code. An updated release should retain a traceable source reference and recompute destination hashes. Do not infer redistribution rights from public download access.

Use the issue and pull-request templates once the repository is published. The software uses MIT. The software citation credits Kamiar Khayambashi and Negin Alemazkoor, University of Virginia. Confirm copyright ownership independently of authorship before release.

# Backend Architecture Notes

These notes exist to prevent future refactors and audits from “fixing” intentional design choices.

## Shared root-level utilities (intentional)

The backend depends on a shared root-level `utils/` package (for example: `utils.logger`).

- This package is intentionally **not** vendored inside `backend/` or `dt_backend/`.
- It is shared across shards, so changes apply consistently.

**Deployment requirement:** ensure the project root is on `PYTHONPATH` (or install the project as a package) so `import utils...` resolves correctly.

## Packaging hygiene

When packaging / shipping the backend, exclude build artifacts such as:

- `__pycache__/`
- `*.pyc`

These are runtime cache files and should not be treated as source.

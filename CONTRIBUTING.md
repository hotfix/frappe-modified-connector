# Contributing

Thanks for helping to improve Modified Connector. Bug reports, ideas and pull requests are all welcome.
For bugs and feature requests, please use the
[issue templates](https://github.com/hotfix/frappe-modified-connector/issues/new/choose). For larger changes,
open an issue first so we can agree on the approach before you spend time on it.

## Development setup

You need a Frappe v16 bench with ERPNext v16 (see the
[Frappe installation guide](https://docs.frappe.io/framework/user/en/installation)).

```bash
cd frappe-bench
bench get-app erpnext --branch version-16
bench get-app https://github.com/hotfix/frappe-modified-connector
bench new-site dev.localhost --install-app erpnext
bench --site dev.localhost install-app modified_connector
bench --site dev.localhost set-config allow_tests true
bench build --app modified_connector
```

To work on your fork, clone it into `apps/modified_connector` instead (or use `bench get-app` with the URL of
your fork). Optionally install [integration_log](https://github.com/hotfix/frappe-integration-log) on the same
site to test the Integration Log hooks.

Use a separate development site. The tests create and delete shops, items, customers and invoices.

## Running the tests

```bash
bench --site dev.localhost run-tests --app modified_connector
```

A single module:

```bash
bench --site dev.localhost run-tests --module modified_connector.tests.test_order_import
```

The tests do not need a real shop: the shop API is mocked. CI runs the same suite on every push and pull
request (see `.github/workflows/ci.yml`).

## Code style

The code is checked with [ruff](https://docs.astral.sh/ruff/) 0.17.6, the version used in CI:

```bash
pip install ruff==0.17.6
ruff check .
ruff format --check .
```

Python files are indented with tabs (see `pyproject.toml`).

## Translations

German translations live in `modified_connector/locale/de.po`.

- Wrap user-facing strings in `_()` (Python) or `__()` (JavaScript).
- **Never override core msgids.** Do not add a msgid to `de.po` that Frappe or ERPNext already translate;
  that would change the wording in every other app on the site. Use a different source string or a context
  instead. `tests/test_translations.py` checks this.
- After changing strings:

  ```bash
  bench --site dev.localhost generate-pot-file --app modified_connector
  bench --site dev.localhost update-po-files --app modified_connector
  bench compile-po-to-mo --app modified_connector
  ```

## Documentation

If you change behaviour, fields or buttons, update `README.md` and the German setup guide
`modified_connector/docs/setup-guide-de.md`. `tests/test_guide.py` checks that the guide mentions every field
and button of the shop form by its German label.

## Commits and pull requests

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `feat: ...`,
  `fix: ...`, `docs: ...`, `test: ...`, `ci: ...`, `chore: ...`, optionally with a scope such as
  `fix(import): ...`. Use the imperative and keep the first line short.
- One logical change per pull request, with tests for new behaviour and bug fixes.
- Never commit credentials, shop URLs or personal data, also not in tests or fixtures.

By contributing you agree that your contribution is licensed under the GPLv3, like the rest of the project.

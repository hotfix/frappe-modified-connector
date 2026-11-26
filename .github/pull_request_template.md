## What and why

<!-- What does this change, and which problem does it solve? Link the issue, e.g. "Fixes #12". -->

## How it was tested

<!-- Tests added or changed, and manual checks (site, steps). -->

## Checklist

- [ ] `ruff check .` and `ruff format --check .` pass (ruff 0.17.6).
- [ ] `bench --site <test-site> run-tests --app modified_connector` passes.
- [ ] README and German setup guide are updated if behaviour, fields or buttons changed.
- [ ] New user-facing strings are translated in `locale/de.po` without redefining Frappe or ERPNext msgids.
- [ ] No credentials, shop URLs or personal data in code, tests or fixtures.

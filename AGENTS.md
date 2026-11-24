# VOLPAROSSA Browser

Follow the [shared development workflow](https://github.com/VOLPAROSSA/volparossa/blob/main/docs/development/WORKFLOW.md).

Use the [Firefox integration contract](docs/CORE_INTEGRATION.md) for changes
at the Core boundary. Preserve the browser-specific kill switch (off by
default) and test fallback behavior in both settings when changing routing.
Keep private page context within its authorized scope. Preserve upstream
revisions and notices as described in [Firefox provenance](docs/FIREFOX_PROVENANCE.md).

# VOLPAROSSA Browser

Follow the [shared development workflow](https://github.com/VOLPAROSSA/volparossa/blob/main/docs/development/CODEX_WORKFLOW.md) for Codex, OpenCode and other coding agents.
Start with the user's task and the relevant part of the [README](README.md).
Choose available models, reasoning effort, tools and any delegation according
to the task's complexity, risk and cost. Use targeted checks. Run builds and
runtime trials on approved disposable hosted runners.

Use the [Firefox integration contract](docs/CORE_INTEGRATION.md) for changes
at the Core boundary. Preserve the browser-specific kill switch (off by
default) and test fallback behavior in both settings when changing routing.
Keep private page context within its authorized scope. Preserve upstream
revisions and notices as described in [Firefox provenance](docs/FIREFOX_PROVENANCE.md).

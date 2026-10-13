# AGENTS.md — agent instructions

## Commits

- Never add `Co-authored-by:` trailers for tools, bots, or AI agents — in particular
  `Co-authored-by: CommandCodeBot <noreply@commandcode.ai>`. Commits are authored by
  the repository owner only, unless explicitly requested otherwise.
- Use the repository's configured git identity (`user.name` / `user.email`). Do not
  override the author or committer.
- Before pushing, check new commits for tool/bot co-author trailers and strip them.

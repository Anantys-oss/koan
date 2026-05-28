# Tool Output Optimization — RTK

`rtk` is installed on this host. It compresses common dev-command output 60-90% before you read it. Prefer it over the raw command whenever an `rtk` filter exists. The unfiltered output is auto-saved on failure, so nothing is lost.

## Use `rtk <cmd>` for these

- Git: `rtk git status`, `rtk git log`, `rtk git diff`, `rtk git add`, `rtk git commit`, `rtk git push`, `rtk git pull`
- Files: `rtk ls`, `rtk read <file>`, `rtk find <glob>`, `rtk grep <pattern>`, `rtk diff a b`
- GitHub: `rtk gh pr list`, `rtk gh pr view`, `rtk gh issue list`, `rtk gh run list`
- Tests: `rtk pytest`, `rtk jest`, `rtk vitest`, `rtk cargo test`, `rtk go test`, `rtk rspec`, `rtk test <any-test-cmd>`
- Build/lint: `rtk lint`, `rtk tsc`, `rtk ruff check`, `rtk cargo build`, `rtk cargo clippy`, `rtk golangci-lint run`
- Containers: `rtk docker ps`, `rtk docker logs`, `rtk kubectl pods`, `rtk kubectl logs`
- Logs/data: `rtk log <file>`, `rtk json <file>`, `rtk err <cmd>`

If a command has no rtk filter, run it raw — rtk only intercepts known commands.

## Meta commands (always raw, not via filter)

- `rtk gain` — show token-savings analytics
- `rtk discover` — find missed savings opportunities

## Notes

- `Read` / `Glob` / `Grep` Claude Code tools bypass rtk. For large files or wide searches, prefer `rtk read <file>` or `rtk grep <pattern>` via Bash.
- Never pipe through `cat -n` or similar — rtk has already filtered.

## Execution model: you run one-shot — finish result-bearing work before ending your turn

Each mission is a **single non-interactive session**. The moment you end your
turn (a final message with no tool call), the session exits and Kōan finalizes
the mission. **There is no event loop afterward.** "Armed monitors", "I'll
report when it finishes", scheduled wake-ups, and any deferred re-invocation
**do not work** — that work is silently dropped, the backgrounded process is
killed, and the mission is marked Done without the result.

**Never end your turn while a result you owe is still pending.** If the mission
asks you to run something and report its output, the command must finish — and
you must read its result — **before** you write your conclusion.

**How to wait for long commands.** A single foreground command may run up to the
Bash tool timeout. For commands that fit within it, run them in the foreground
and block. For longer ones, run in the background **and poll within the same
turn** — loop `sleep`-then-check on a completion sentinel until it finishes, then
read the result. Do **not** background a command and then stop. You have the whole
mission budget (`mission_timeout`, default **60 minutes**), so blocking or polling
for many minutes inside one turn is expected.

Reuse the redirect-to-file pattern (as with `make test` above) so a large
command's output doesn't burn tokens — read the file only when you need the detail:

```bash
log=$(mktemp "${TMPDIR:-/tmp}/koan-cmd-XXXXXX")
( <long-running command> > "$log" 2>&1; echo "DONE:$?" >> "$log" ) &
# Poll within THIS turn until the sentinel appears — do not end your turn yet.
until grep -q '^DONE:' "$log"; do sleep 30; done
exit_code=$(grep '^DONE:' "$log" | tail -1 | cut -d: -f2)
tail -n 40 "$log"   # report the result from here
```


# Audit Missions — Issue Tracker Follow-up

When your mission contains the word "audit" (security audit, code audit, etc.), you have
additional responsibilities beyond writing a report:

1. **Document findings clearly** in your journal entry with severity levels (critical/high/medium/low)

2. **Evaluate actionability**: At the end of the audit, ask yourself:
   - Are there findings that require follow-up work?
   - Is there technical debt or risk that shouldn't be forgotten?
   - Would a tracker issue help record the work needed?

3. **Create a tracker issue when appropriate**: If your audit reveals issues worth tracking, use Koan's provider-neutral issue helper:
   ```bash
   cd {PROJECT_PATH}
   issue_body=$(mktemp /tmp/koan-audit-issue-XXXXXX)
   cat > "$issue_body" <<'EOF'
   ## Audit Findings — [date]

   [Summary of key findings]

   ### Action Items
   - [ ] [item 1]
   - [ ] [item 2]

   ### Details
   [Link to journal entry or branch with full report]

   ---
   🤖 Created by Kōan from audit session
   EOF
   {KOAN_PYTHON} -m app.issue_cli create \
     --project "{PROJECT_NAME}" \
     --project-path "{PROJECT_PATH}" \
     --title "Audit: [summary]" \
     --body-file "$issue_body"
   ```

4. **Skip issue creation when**:
   - The audit found nothing significant
   - All findings are trivial or already known
   - The project has no configured issue tracker
   - The findings were already fixed in the same session

5. **Include the issue URL** in your journal and conclusion message when created.

This ensures audits have lasting impact beyond the session — findings become tracked work items.

# Mission Spec — PR Context

If a mission spec was included in your prompt (under "Mission Spec"), reference its
key decisions in the PR description's **Why** and **How** sections — don't paste the
full spec, just the relevant context.

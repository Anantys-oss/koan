- If the local repo is a fork, submit the PR to the upstream repository:
  ```bash
  gh pr create --draft --repo <upstream-owner>/<repo> --head <fork-owner>:<branch> --title "..." --body "..."
  ```
- PRs are **always draft**. Never create a non-draft PR.

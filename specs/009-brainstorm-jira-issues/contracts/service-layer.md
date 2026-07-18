# Contract: Issue-tracker service layer additions

These extend the provider-neutral contract in
`specs/components/issue-tracking.md`. **Declared architectural change** — new
members on the `IssueTracker` ABC and the `app.issue_tracker` service layer.

## `IssueTracker` ABC (base.py) — new members

Both are **concrete** (non-abstract) with safe defaults so existing out-of-tree
subclasses keep working.

```python
def update_issue(self, url: str, body: str) -> bool:
    """Rewrite an existing issue's body/description. Return True on success.
    Default: return False (backend does not support updates)."""

def link_issues(self, parent_url: str, child_url: str,
                link_type: str = "Relates") -> bool:
    """Create a native tracker link parent→child. Return True on success.
    Default: return False (no-op; provider expresses linkage in body text)."""
```

### GitHubIssueTracker

- `update_issue(url, body)` → parse issue number from `url`, delegate to
  `app.github.issue_edit(number, body, cwd=project_path, repo=self.repo)`.
  Returns True unless `issue_edit` raises (caught → False).
- `link_issues(...)` → **no-op**, returns `False`. GitHub linkage is already
  expressed via `#N` references and the master task list.

### JiraIssueTracker

- `update_issue(url, body)` → `jira_update_issue_description(parse_jira_url(url),
  body)` (PUT `/rest/api/3/issue/{key}` with ADF description). Returns the
  transport's success boolean.
- `link_issues(parent_url, child_url, link_type="Relates")` →
  `jira_link_issues(parse_jira_url(parent_url), parse_jira_url(child_url),
  link_type)` (POST `/rest/api/3/issueLink`). Returns success boolean.

## Service layer (`app.issue_tracker.__init__`) — new functions

```python
def update_issue(url, body, project_name="", project_path="") -> bool:
    """Route to the owning client for `url` and rewrite its body."""
    return client_for_url(url, project_name=project_name,
                          project_path=project_path).update_issue(url, body)

def link_issues(parent_url, child_url, link_type="Relates",
                project_name="", project_path="") -> bool:
    """Route to the owning client for `parent_url` and create a native link."""
    return client_for_url(parent_url, project_name=project_name,
                          project_path=project_path).link_issues(
        parent_url, child_url, link_type)
```

**Invariants**:
- Callers (skills) use the service functions, never a backend directly.
- Both functions inherit non-fatal semantics from their backends — a False
  return means "not done", never an exception the caller must handle (backends
  catch transport errors and return False).

## Jira transport (`app.jira_notifications`) — new functions

```python
def markdown_to_adf(text: str) -> dict:
    """Convert brainstorm's markdown subset to an ADF `doc`. Superset of the
    old `_text_to_adf` fallback: unmodeled lines still become paragraphs."""

def jira_update_issue_description(issue_key: str, body_text: str) -> bool:
    """PUT /rest/api/3/issue/{key} with {fields:{description: markdown_to_adf(...)}}.
    Returns False on transport failure (never raises)."""

def jira_link_issues(outward_key: str, inward_key: str,
                     link_type: str = "Relates") -> bool:
    """POST /rest/api/3/issueLink relating outward→inward. False on failure."""
```

- `jira_create_issue` is changed to build `description` via `markdown_to_adf`
  (was `_text_to_adf`).
- `jira_add_comment` / `jira_edit_comment` are **unchanged** (still
  `_text_to_adf`) to preserve the human-`/comment` invariant (FR-009).

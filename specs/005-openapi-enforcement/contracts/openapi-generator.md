# Contract — `app.api.openapi_gen`

## Module: `koan/app/api/openapi_gen.py`

### `build_spec(app: Flask) -> dict`

- **Input**: a Flask app (typically `create_app()`), whose `url_map` and `view_functions`
  are read. No network, no port, no token.
- **Output**: a plain `dict` OpenAPI 3.1 document (see data-model.md).
- **Guarantees**:
  - Every registered route (excluding `HEAD`/`OPTIONS` methods) appears exactly once under
    `paths[<openapi-path>][<method>]`.
  - No path appears that is not a registered route.
  - An operation carries no `security` key (inherits the global `bearerAuth`) iff its view
    function has a truthy `_koan_requires_token`; otherwise it carries `security: []`.
  - Pure: equal route tables produce equal dicts.

### `dump_yaml(spec: dict) -> str`

- Serializes with `yaml.safe_dump(spec, sort_keys=True, default_flow_style=False,
  allow_unicode=True, width=1 << 30)`, prefixed by a fixed do-not-edit header comment.
  The effectively-infinite `width` disables line-folding; PyYAML's fold position is
  width- and version-sensitive, so a finite width would make the byte-diff drift gate
  flap between a dev machine and CI.
- **Guarantee**: equal `spec` dicts → byte-identical strings.

### `generate(output_path: Path) -> None`

- Builds the app (temp `koan_root`), `build_spec`, `dump_yaml`, writes `output_path`.

### `check(output_path: Path) -> int`

- Builds the current text and compares to the file at `output_path`.
- Returns `0` if identical; `1` if different or the file is missing, after printing to stderr:
  ```
  ERROR: <output_path> is out of date with the REST API code.
  Regenerate and commit it:
      make openapi
      git add koan/openapi.yaml && git commit
  ```

### CLI: `python -m app.api.openapi_gen [--output PATH] [--check]`

- `--output PATH` (default `openapi.yaml`, relative to CWD).
- Without `--check`: writes the document (exit 0).
- With `--check`: runs `check()`, exits with its return code.

## Makefile contract

```make
openapi: setup        # regenerate koan/openapi.yaml from the live app
openapi-check: setup  # fail if koan/openapi.yaml drifts from the code
```

Both run inside `koan/` with `PYTHONPATH=.` and a throwaway `KOAN_ROOT`, mirroring the
`webhook` target's invocation style.

## CI contract (`.github/workflows/openapi.yml`)

- Trigger: `pull_request` to `main`, `paths:` = the API-defining file set (data-model.md).
- Single job: checkout → setup Python 3.14 → `pip install -r koan/requirements.txt` →
  run `python -m app.api.openapi_gen --check --output openapi.yaml` in `koan/`.
- Job **does not run** when no `paths:` glob matches (FR-008).
- On drift: non-zero exit + the fix instruction on the log (FR-010).

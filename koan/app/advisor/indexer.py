"""Indexer — semantic indexing of GitHub + GitLab repos.

Provides:
- index_repo: generate functional summary, detect technologies, resolve owner
- index_repo_files: summarize and embed significant files into SQLite
- run_full_scan: scan all known repos
- run_incremental_scan: scan only repos modified since last index
"""

import json
import logging
import subprocess
import time
from datetime import datetime, timezone

from app.advisor.helpers import (
    get_db,
    load_repo_index,
    save_repo_index,
    summarize_with_llm,
    embed_text,
    serialize_embedding,
    upsert_vec_embedding,
    PLATFORM_GITHUB,
    PLATFORM_GITLAB,
)
from app.advisor.heuristics import scan_for_data_patterns

logger = logging.getLogger("advisor.indexer")

REPO_SUMMARY_PROMPT = """Analyse ce dépôt de code et fournis un résumé fonctionnel en 3-5 phrases en français.
Décris ce que fait ce projet d'un point de vue utilisateur/business (pas technique).

Nom du repo : {repo_name}
README :
{readme}

Structure des fichiers :
{file_tree}"""

FILE_SUMMARY_PROMPT = """Résume la fonctionnalité de ce fichier en 2-3 phrases en français.
Décris ce qu'il fait d'un point de vue fonctionnel, pas technique.

Fichier : {file_path}
Contenu :
{content}"""

CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".php", ".rb", ".go",
                   ".rs", ".java", ".kt", ".swift", ".vue", ".css", ".scss"}


def _detect_technologies(file_tree: str, file_contents: dict[str, str]) -> list[str]:
    """Detect technologies from file names and package files."""
    techs = set()

    tech_markers = {
        "requirements.txt": "Python", "setup.py": "Python",
        "pyproject.toml": "Python", "Pipfile": "Python",
        "package.json": "JavaScript", "tsconfig.json": "TypeScript",
        "composer.json": "PHP", "Gemfile": "Ruby",
        "go.mod": "Go", "Cargo.toml": "Rust",
        "pom.xml": "Java", "build.gradle": "Java",
        "Dockerfile": "Docker", "docker-compose.yml": "Docker",
    }
    for marker, tech in tech_markers.items():
        if marker in file_tree:
            techs.add(tech)

    ext_techs = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".php": "PHP", ".rb": "Ruby", ".go": "Go", ".rs": "Rust",
        ".java": "Java", ".kt": "Kotlin", ".swift": "Swift",
        ".vue": "Vue.js", ".jsx": "React", ".tsx": "React",
    }
    for ext, tech in ext_techs.items():
        if ext in file_tree:
            techs.add(tech)

    for name, content in file_contents.items():
        if name == "package.json" and content:
            try:
                pkg = json.loads(content)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                for lib, tech in [("react", "React"), ("vue", "Vue.js"),
                                  ("next", "Next.js"), ("express", "Express")]:
                    if lib in deps:
                        techs.add(tech)
            except (json.JSONDecodeError, TypeError):
                pass

    return sorted(techs)


def _extract_dependencies(file_contents: dict[str, str]) -> list[str]:
    """Extract main dependencies from package files."""
    deps = []
    for name, content in file_contents.items():
        if not content:
            continue
        if name == "requirements.txt":
            for line in content.splitlines()[:20]:
                line = line.strip()
                if line and not line.startswith("#"):
                    pkg = line.split("==")[0].split(">=")[0].split("<=")[0].strip()
                    if pkg:
                        deps.append(pkg)
        elif name == "package.json":
            try:
                pkg = json.loads(content)
                deps.extend(list(pkg.get("dependencies", {}).keys())[:15])
            except (json.JSONDecodeError, TypeError):
                pass
    return deps[:20]


# ── GitHub API helpers ───────────────────────────────────────────────

def _gh_api(path: str) -> str | None:
    """Call GitHub API via gh CLI, return stdout or None."""
    try:
        result = subprocess.run(
            ["gh", "api", path],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _fetch_github_file(org: str, repo_name: str, path: str) -> str:
    """Fetch a file from GitHub using gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{org}/{repo_name}/contents/{path}",
             "-H", "Accept: application/vnd.github.raw+json"],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _fetch_github_repo_data(org: str, repo_name: str) -> tuple[str, str, list[dict]]:
    """Fetch repo metadata, README, and tree from GitHub in one pass.

    Returns (default_branch, readme_content, tree_items).
    """
    default_branch = "main"
    raw = _gh_api(f"repos/{org}/{repo_name}")
    if raw:
        try:
            data = json.loads(raw)
            default_branch = data.get("default_branch", "main")
        except json.JSONDecodeError:
            pass

    readme = _fetch_github_file(org, repo_name, "README.md")

    tree = []
    raw_tree = _gh_api(f"repos/{org}/{repo_name}/git/trees/{default_branch}")
    if raw_tree:
        try:
            tree = json.loads(raw_tree).get("tree", [])
        except json.JSONDecodeError:
            pass

    return default_branch, readme, tree


# ── GitLab API helpers ───────────────────────────────────────────────

def _get_gitlab_project(gl_client, repo_name: str, repo_info: dict):
    """Get a GitLab project object, using cached ID if available."""
    project_id = repo_info.get("id")
    if project_id:
        return gl_client._gl.projects.get(project_id)

    projects = gl_client.list_group_projects()
    for p in projects:
        if p["name"] == repo_name:
            return gl_client._gl.projects.get(p["id"])
    return None


def _fetch_gitlab_repo_data(gl_client, repo_name: str, repo_info: dict
                            ) -> tuple[str, str, list[dict], object | None]:
    """Fetch repo data from GitLab in one pass.

    Returns (default_branch, readme_content, tree_items, project_obj).
    """
    project = _get_gitlab_project(gl_client, repo_name, repo_info)
    if not project:
        return "main", "", [], None

    default_branch = getattr(project, "default_branch", "main") or "main"

    readme = ""
    try:
        f = project.files.get(file_path="README.md", ref=default_branch)
        readme = f.decode().decode("utf-8", errors="replace")
    except Exception:
        pass

    tree = []
    try:
        items = project.repository_tree(ref=default_branch, recursive=True,
                                        per_page=100, get_all=True)
        tree = [{"path": i["path"], "type": i["type"]} for i in items]
    except Exception:
        pass

    return default_branch, readme, tree, project


# ── Core indexing ────────────────────────────────────────────────────

def index_repo(repo_info: dict, config: dict,
               prefetched: dict | None = None) -> dict:
    """Index a complete repo: functional summary, technologies, dependencies, owner.

    Args:
        repo_info: dict from watcher repos.yaml
        config: advisor section of config.yaml
        prefetched: optional pre-fetched data to avoid double API calls

    Returns:
        RepoIndexEntry dict
    """
    platform = repo_info.get("platform", PLATFORM_GITHUB)
    repo_name = repo_info.get("name", "")
    url = repo_info.get("url", "")

    readme_content = ""
    file_tree_str = ""
    package_files = {}

    if prefetched:
        readme_content = prefetched.get("readme", "")
        file_tree_str = prefetched.get("file_tree_str", "")
        package_files = prefetched.get("package_files", {})
    elif platform == PLATFORM_GITHUB:
        watcher_cfg = _get_watcher_config_cached()
        org = watcher_cfg.get("github", {}).get("org", "YourArtOfficial")
        _, readme_content, tree = _fetch_github_repo_data(org, repo_name)
        file_tree_str = "\n".join(
            item.get("path", "") for item in tree if item.get("type") == "blob"
        )
        for pkg_file in ["requirements.txt", "package.json", "pyproject.toml"]:
            content = _fetch_github_file(org, repo_name, pkg_file)
            if content:
                package_files[pkg_file] = content
    elif platform == PLATFORM_GITLAB:
        try:
            from app.watcher.gitlab_client import GitLabClient
            watcher_cfg = _get_watcher_config_cached()
            gl_client = GitLabClient.from_config(watcher_cfg)
            _, readme_content, tree, _ = _fetch_gitlab_repo_data(
                gl_client, repo_name, repo_info
            )
            file_tree_str = "\n".join(
                item["path"] for item in tree if item.get("type") == "blob"
            )
            for pkg_file in ["requirements.txt", "package.json", "composer.json"]:
                try:
                    project = _get_gitlab_project(gl_client, repo_name, repo_info)
                    if project:
                        f = project.files.get(file_path=pkg_file, ref="main")
                        content = f.decode().decode("utf-8", errors="replace")
                        if content:
                            package_files[pkg_file] = content
                except Exception:
                    pass
        except (ImportError, ValueError) as e:
            logger.error("GitLab client error: %s", e)

    technologies = _detect_technologies(file_tree_str, package_files)
    dependencies = _extract_dependencies(package_files)

    summary = ""
    if readme_content or file_tree_str:
        summary = summarize_with_llm(
            REPO_SUMMARY_PROMPT.format(
                repo_name=repo_name,
                readme=readme_content[:3000] if readme_content else "(pas de README)",
                file_tree=file_tree_str[:2000],
            ),
            config,
        )

    owner = ""
    owner_name = None
    owner_type = "unknown"
    contributors = repo_info.get("contributors", [])
    if contributors:
        owner = contributors[0]
        try:
            from app.watcher.helpers import classify_author
            owner_type, owner_name = classify_author(owner, platform)
        except ImportError:
            pass

    now = datetime.now(timezone.utc).isoformat()

    return {
        "id": f"{platform}/{repo_name}",
        "platform": platform,
        "name": repo_name,
        "url": url,
        "summary": summary,
        "technologies": technologies,
        "dependencies": dependencies,
        "owner": owner,
        "owner_name": owner_name,
        "owner_type": owner_type,
        "status": repo_info.get("status", "active"),
        "last_commit_at": repo_info.get("last_activity", ""),
        "indexed_at": now,
        "file_count": 0,
    }


def index_repo_files(repo_id: str, files: list[dict], config: dict) -> int:
    """Index significant files from a repo (summaries + embeddings in SQLite).

    Args:
        repo_id: identifier (e.g. github/emailfactory)
        files: list of {path, content} dicts
        config: advisor section

    Returns:
        number of files indexed
    """
    min_lines = config.get("file_min_lines", 50)
    conn = get_db()
    count = 0

    try:
        for file_info in files:
            path = file_info.get("path", "")
            content = file_info.get("content", "")

            if not content or len(content.splitlines()) < min_lines:
                continue

            summary = summarize_with_llm(
                FILE_SUMMARY_PROMPT.format(file_path=path, content=content[:5000]),
                config,
            )
            if not summary:
                continue

            embedding = embed_text(summary, config)
            data_resources = scan_for_data_patterns(content)
            now = datetime.now(timezone.utc).isoformat()

            try:
                embedding_blob = serialize_embedding(embedding) if embedding else None
                conn.execute(
                    """INSERT OR REPLACE INTO file_summaries
                       (repo_id, file_path, summary, embedding, data_resources, indexed_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (repo_id, path, summary, embedding_blob,
                     json.dumps(data_resources), now),
                )

                if embedding:
                    row_id = conn.execute(
                        "SELECT id FROM file_summaries WHERE repo_id=? AND file_path=?",
                        (repo_id, path),
                    ).fetchone()[0]
                    upsert_vec_embedding(
                        conn, "vec_file_summaries", "summary_embedding",
                        row_id, embedding,
                    )

                count += 1
            except Exception as e:
                logger.error("Error indexing file %s/%s: %s", repo_id, path, e)

        conn.commit()
    except Exception as e:
        logger.error("Error in index_repo_files batch: %s", e)

    return count


# ── Progress tracking ────────────────────────────────────────────────

_scan_progress: dict = {}
_scan_notify_fn = None


def set_scan_notify_fn(fn):
    """Set a callback function for scan progress notifications."""
    global _scan_notify_fn
    _scan_notify_fn = fn


def get_scan_progress() -> dict:
    """Return current scan progress (thread-safe read)."""
    return dict(_scan_progress)


def _progress_bar(percent: int, width: int = 20) -> str:
    filled = int(width * percent / 100)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _update_progress(total: int, scanned: int, current_repo: str,
                     files_indexed: int, status: str = "running") -> None:
    """Update scan progress (called during scan loop)."""
    pct = round(scanned / total * 100) if total else 0
    _scan_progress.update({
        "status": status,
        "total_repos": total,
        "repos_scanned": scanned,
        "current_repo": current_repo,
        "files_indexed": files_indexed,
        "percent": pct,
    })
    # Send Google Chat progress notification every 10 repos
    if _scan_notify_fn and scanned > 0 and scanned % 10 == 0 and status == "running":
        bar = _progress_bar(pct)
        msg = f"{bar} {pct}% — {scanned}/{total} repos (en cours: {current_repo})"
        try:
            _scan_notify_fn(msg)
        except Exception:
            pass


def run_full_scan(config: dict) -> dict:
    """Full scan of all repos (GitHub + GitLab).

    Returns:
        {"repos_scanned": int, "files_indexed": int, "duration_s": float}
    """
    start = time.time()
    stats = {"repos_scanned": 0, "files_indexed": 0, "duration_s": 0.0}

    repos = _get_all_repos()
    index_entries = load_repo_index()
    index_by_id = {e["id"]: e for e in index_entries}

    _update_progress(len(repos), 0, "", 0, "running")

    for repo_info in repos:
        repo_name = repo_info.get("name", "?")
        _update_progress(len(repos), stats["repos_scanned"], repo_name,
                         stats["files_indexed"])
        try:
            prefetched, code_files = _prefetch_repo(repo_info, config)
            entry = index_repo(repo_info, config, prefetched=prefetched)
            index_by_id[entry["id"]] = entry

            if code_files:
                file_count = index_repo_files(entry["id"], code_files, config)
                entry["file_count"] = file_count
                stats["files_indexed"] += file_count

            stats["repos_scanned"] += 1
        except Exception as e:
            logger.error("Error scanning repo %s: %s", repo_name, e)
            stats["repos_scanned"] += 1

        # Save index periodically (every 10 repos) to avoid losing work
        if stats["repos_scanned"] % 10 == 0:
            save_repo_index(list(index_by_id.values()))

    save_repo_index(list(index_by_id.values()))
    stats["duration_s"] = round(time.time() - start, 1)
    _update_progress(len(repos), stats["repos_scanned"], "", stats["files_indexed"], "done")
    logger.info("Full scan: %d repos, %d files in %.1fs",
                stats["repos_scanned"], stats["files_indexed"], stats["duration_s"])
    return stats


def run_incremental_scan(config: dict) -> dict:
    """Incremental scan — only repos modified since last index.

    Returns:
        {"repos_scanned": int, "files_indexed": int, "duration_s": float}
    """
    start = time.time()
    stats = {"repos_scanned": 0, "files_indexed": 0, "duration_s": 0.0}

    repos = _get_all_repos()
    index_entries = load_repo_index()
    index_by_id = {e["id"]: e for e in index_entries}

    to_scan = []
    for repo_info in repos:
        platform = repo_info.get("platform", PLATFORM_GITHUB)
        repo_name = repo_info.get("name", "")
        repo_id = f"{platform}/{repo_name}"

        existing = index_by_id.get(repo_id)
        if existing:
            last_activity = repo_info.get("last_activity", "")
            indexed_at = existing.get("indexed_at", "")
            if last_activity and indexed_at and last_activity <= indexed_at:
                continue
        to_scan.append(repo_info)

    _update_progress(len(to_scan), 0, "", 0, "running")

    for repo_info in to_scan:
        repo_name = repo_info.get("name", "?")
        _update_progress(len(to_scan), stats["repos_scanned"], repo_name,
                         stats["files_indexed"])
        try:
            prefetched, code_files = _prefetch_repo(repo_info, config)
            entry = index_repo(repo_info, config, prefetched=prefetched)
            index_by_id[entry["id"]] = entry

            if code_files:
                file_count = index_repo_files(entry["id"], code_files, config)
                entry["file_count"] = file_count
                stats["files_indexed"] += file_count

            stats["repos_scanned"] += 1
        except Exception as e:
            logger.error("Error scanning repo %s: %s", repo_name, e)
            stats["repos_scanned"] += 1

    save_repo_index(list(index_by_id.values()))
    stats["duration_s"] = round(time.time() - start, 1)
    _update_progress(len(to_scan), stats["repos_scanned"], "", stats["files_indexed"], "done")
    logger.info("Incremental scan: %d repos, %d files in %.1fs",
                stats["repos_scanned"], stats["files_indexed"], stats["duration_s"])
    return stats


# ── Internal helpers ─────────────────────────────────────────────────

_watcher_config_cache: dict | None = None


def _get_watcher_config_cached() -> dict:
    """Load watcher config (cached for the scan session)."""
    global _watcher_config_cache
    if _watcher_config_cache is None:
        from app.utils import load_config
        _watcher_config_cache = load_config().get("watcher", {})
    return _watcher_config_cache


def _get_all_repos() -> list[dict]:
    """Load all known repos from watcher repos.yaml + discover GitLab repos.

    Merges repos.yaml with live GitLab project list to ensure
    cross-platform coverage (repos.yaml only tracks repos with commits).
    """
    try:
        from app.watcher.helpers import load_repos, save_repos
        repos = load_repos()
    except ImportError:
        logger.warning("Watcher module not available, no repos to scan")
        return []

    # Discover GitLab repos not yet in repos.yaml
    try:
        watcher_cfg = _get_watcher_config_cached()
        gitlab_cfg = watcher_cfg.get("gitlab", {})
        if gitlab_cfg.get("group"):
            from app.watcher.gitlab_client import GitLabClient
            gl_client = GitLabClient.from_config(watcher_cfg)
            projects = gl_client.list_group_projects()

            existing_gitlab = {
                r["name"] for r in repos if r.get("platform") == PLATFORM_GITLAB
            }
            added = 0
            for project in projects:
                if project["name"] not in existing_gitlab:
                    repos.append({
                        "name": project["name"],
                        "platform": PLATFORM_GITLAB,
                        "url": project["web_url"],
                        "status": "active",
                        "language": None,
                        "last_activity": project.get("last_activity_at", ""),
                        "contributors": [],
                        "webhook_active": False,
                        "id": project["id"],
                    })
                    added += 1

            if added:
                logger.info("Discovered %d new GitLab repos (total: %d)", added, len(repos))
                save_repos(repos)
    except (ImportError, ValueError) as e:
        logger.warning("GitLab discovery skipped: %s", e)
    except Exception as e:
        logger.error("GitLab discovery error: %s", e)

    return repos


def _prefetch_repo(repo_info: dict, config: dict
                   ) -> tuple[dict, list[dict]]:
    """Prefetch repo data and code files in one pass to avoid double API calls.

    Returns (prefetched_data, code_files).
    """
    platform = repo_info.get("platform", PLATFORM_GITHUB)
    repo_name = repo_info.get("name", "")
    min_lines = config.get("file_min_lines", 50)
    prefetched = {}
    code_files = []

    if platform == PLATFORM_GITHUB:
        watcher_cfg = _get_watcher_config_cached()
        org = watcher_cfg.get("github", {}).get("org", "YourArtOfficial")

        default_branch, readme, tree = _fetch_github_repo_data(org, repo_name)

        file_tree_str = "\n".join(
            item.get("path", "") for item in tree if item.get("type") == "blob"
        )

        package_files = {}
        for pkg_file in ["requirements.txt", "package.json", "pyproject.toml"]:
            content = _fetch_github_file(org, repo_name, pkg_file)
            if content:
                package_files[pkg_file] = content

        prefetched = {
            "readme": readme,
            "file_tree_str": file_tree_str,
            "package_files": package_files,
        }

        source_files = [
            item for item in tree
            if item.get("type") == "blob"
            and any(item.get("path", "").endswith(ext) for ext in CODE_EXTENSIONS)
        ]
        for item in source_files[:30]:
            path = item.get("path", "")
            content = _fetch_github_file(org, repo_name, path)
            if content and len(content.splitlines()) >= min_lines:
                code_files.append({"path": path, "content": content})

    elif platform == PLATFORM_GITLAB:
        try:
            from app.watcher.gitlab_client import GitLabClient
            watcher_cfg = _get_watcher_config_cached()
            gl_client = GitLabClient.from_config(watcher_cfg)

            default_branch, readme, tree, project = _fetch_gitlab_repo_data(
                gl_client, repo_name, repo_info
            )

            file_tree_str = "\n".join(
                item["path"] for item in tree if item.get("type") == "blob"
            )

            package_files = {}
            if project:
                for pkg_file in ["requirements.txt", "package.json", "composer.json"]:
                    try:
                        f = project.files.get(file_path=pkg_file, ref=default_branch)
                        content = f.decode().decode("utf-8", errors="replace")
                        if content:
                            package_files[pkg_file] = content
                    except Exception:
                        pass

            prefetched = {
                "readme": readme,
                "file_tree_str": file_tree_str,
                "package_files": package_files,
            }

            if project:
                source_files = [
                    item for item in tree
                    if item.get("type") == "blob"
                    and any(item["path"].endswith(ext) for ext in CODE_EXTENSIONS)
                ]
                for item in source_files[:30]:
                    path = item["path"]
                    try:
                        f = project.files.get(file_path=path, ref=default_branch)
                        content = f.decode().decode("utf-8", errors="replace")
                        if content and len(content.splitlines()) >= min_lines:
                            code_files.append({"path": path, "content": content})
                    except Exception:
                        pass
        except (ImportError, ValueError) as e:
            logger.error("GitLab client error for prefetch: %s", e)

    return prefetched, code_files

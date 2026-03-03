"""Credential Scanner — detect-secrets integration for leak detection.

Wraps the detect-secrets library to scan files and repos for credentials.
Supports baseline management to filter known/accepted findings.
"""

import json
import logging
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("credential_vault.scanner")

SEVERITY_MAP = {
    "ArtifactoryDetector": "HIGH",
    "AWSKeyDetector": "HIGH",
    "AzureStorageKeyDetector": "HIGH",
    "BasicAuthDetector": "HIGH",
    "CloudantDetector": "HIGH",
    "DiscordBotTokenDetector": "HIGH",
    "GitHubTokenDetector": "HIGH",
    "HexHighEntropyString": "MEDIUM",
    "Base64HighEntropyString": "MEDIUM",
    "IbmCloudIamDetector": "HIGH",
    "IbmCosHmacDetector": "HIGH",
    "JwtTokenDetector": "MEDIUM",
    "KeywordDetector": "MEDIUM",
    "MailchimpDetector": "HIGH",
    "NpmDetector": "HIGH",
    "PrivateKeyDetector": "HIGH",
    "SendGridDetector": "HIGH",
    "SlackDetector": "HIGH",
    "SoftlayerDetector": "HIGH",
    "SquareOAuthDetector": "HIGH",
    "StripeDetector": "HIGH",
    "TwilioKeyDetector": "HIGH",
}

EXCLUDE_PATTERNS = [
    "*.pyc", "__pycache__", ".git", "node_modules",
    "*.min.js", "*.map", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "*.woff", "*.woff2", "*.ttf",
]


def scan_directory(dir_path: str, exclude_patterns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Scan a directory for credentials using detect-secrets."""
    exclude = exclude_patterns or EXCLUDE_PATTERNS

    try:
        cmd = ["detect-secrets", "scan", dir_path, "--json"]
        for pattern in exclude:
            cmd.extend(["--exclude-files", pattern])

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )

        if result.returncode != 0 and not result.stdout:
            logger.error("detect-secrets failed: %s", result.stderr)
            return []

        data = json.loads(result.stdout)
        return _parse_scan_results(data, dir_path)

    except FileNotFoundError:
        logger.error("detect-secrets not installed. Run: pip install detect-secrets")
        return []
    except subprocess.TimeoutExpired:
        logger.error("Scan timeout for %s", dir_path)
        return []
    except json.JSONDecodeError:
        logger.error("Invalid JSON output from detect-secrets")
        return []


def scan_repo(repo_name: str, clone_path: Optional[str] = None,
              org: str = "YourArtOfficial") -> List[Dict[str, Any]]:
    """Clone a repo and scan it for credentials.

    Args:
        repo_name: GitHub repo name (e.g. 'fetching')
        clone_path: Optional path to existing clone. If None, clones to temp dir.
        org: GitHub organization name.

    Returns:
        List of findings with severity, file, line, type.
    """
    if clone_path and Path(clone_path).exists():
        return scan_directory(clone_path)

    with tempfile.TemporaryDirectory(prefix=f"scan-{repo_name}-") as tmpdir:
        try:
            subprocess.run(
                ["gh", "repo", "clone", f"{org}/{repo_name}", tmpdir],
                capture_output=True, text=True, timeout=60, check=True
            )
        except subprocess.CalledProcessError as e:
            logger.error("Failed to clone %s: %s", repo_name, e.stderr)
            return []
        except FileNotFoundError:
            logger.error("gh CLI not installed")
            return []

        return scan_directory(tmpdir)


def _parse_scan_results(data: Dict[str, Any], base_path: str) -> List[Dict[str, Any]]:
    """Parse detect-secrets JSON output into our finding format."""
    findings = []
    results = data.get("results", {})

    for filepath, secrets in results.items():
        rel_path = filepath
        if filepath.startswith(base_path):
            rel_path = filepath[len(base_path):].lstrip("/")

        for secret in secrets:
            detector = secret.get("type", "Unknown")
            finding = {
                "file": rel_path,
                "line": secret.get("line_number", 0),
                "type": detector,
                "severity": classify_finding(detector),
                "hashed_secret": secret.get("hashed_secret", ""),
            }
            findings.append(finding)

    return findings


def classify_finding(detector_type: str) -> str:
    """Map a detect-secrets detector to a severity level."""
    return SEVERITY_MAP.get(detector_type, "MEDIUM")


def load_baseline(path: str) -> Dict[str, Any]:
    """Load a baseline file of known/accepted findings."""
    p = Path(path)
    if not p.exists():
        return {"known_hashes": set()}
    with open(p) as f:
        data = json.load(f)
    return {
        "known_hashes": set(data.get("known_hashes", [])),
        "updated_at": data.get("updated_at"),
    }


def update_baseline(path: str, findings: List[Dict[str, Any]]) -> int:
    """Update baseline with current findings. Returns count of new entries."""
    existing = load_baseline(path)
    known = existing["known_hashes"]
    new_hashes = {f["hashed_secret"] for f in findings if f.get("hashed_secret")}
    added = new_hashes - known
    all_hashes = sorted(known | new_hashes)

    data = {
        "known_hashes": all_hashes,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(all_hashes),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info("Baseline updated: %d new, %d total", len(added), len(all_hashes))
    return len(added)


def filter_new_findings(findings: List[Dict[str, Any]],
                        baseline: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Filter out findings that are in the baseline."""
    known = baseline.get("known_hashes", set())
    return [f for f in findings if f.get("hashed_secret") not in known]

"""Live SOP Runtime Compiler.

Monitors SOP YAML files for changes. When a file changes:
1. Reloads all SOP fixtures
2. Runs Normalizer (load_normalized_project_sops) for .md or load_project_sop for .yaml
3. Runs SopMonitoringPlanCompiler
4. Hot-swaps the runtime monitoring plan atomically

No restart required.

R26: Part B — SOPWatcher
R27: Canonical YAML source support — config/project_sops/
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sop_hub.models.project_sop import (
    NormalizedProjectSOP,
    ProjectSOP,
    load_normalized_project_sops,
    load_project_sop,
)
from sop_hub.sop.monitoring_plan_compiler import SopMonitoringPlanCompiler
from sop_hub.sop.monitoring_plan_preview import normalized_project_sops_to_compiler_input

logger = logging.getLogger("sop_hub.sop_watcher")


@dataclass
class SopRuntime:
    """Hot-swappable SOP runtime state.

    Holds the compiled monitoring plan and metadata about loaded projects.
    The plan is replaced atomically on reload — no stale reads inbetween.
    """

    sop_dir: Path
    plan: dict[str, Any] = field(default_factory=dict)
    normalized_projects: list[NormalizedProjectSOP] = field(default_factory=list)
    _file_hashes: dict[str, str] = field(default_factory=dict)
    loaded_projects: list[str] = field(default_factory=list)
    last_reload: str = ""
    sop_hash: str = ""

    def compute_dir_hash(self) -> str:
        """Compute a composite hash of all SOP files (both .yaml and .md)."""
        h = hashlib.sha256()
        for pattern in ("*.yaml", "*.md"):
            for f in sorted(self.sop_dir.glob(pattern)):
                if f.is_file():
                    h.update(f.read_bytes())
        return h.hexdigest()[:16] if h.digest() != b"\x00" * 32 else ""

    def reload(self) -> bool:
        """Reload SOPs from disk.

        Supports both .yaml (R27 — config/project_sops/) and .md (legacy fixtures).
        Returns True if the plan changed (hash differs), False otherwise.
        """
        new_hash = self.compute_dir_hash()
        if new_hash and new_hash == self.sop_hash:
            return False

        yaml_files = list(self.sop_dir.glob("*.yaml"))
        if yaml_files:
            self._reload_from_yaml(yaml_files)
        else:
            self._reload_from_md()

        self.sop_hash = new_hash
        self.last_reload = (
            datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        )

        # Recompute per-file hashes for detailed diagnostics
        self._file_hashes = {}
        for pattern in ("*.yaml", "*.md"):
            for f in sorted(self.sop_dir.glob(pattern)):
                if f.is_file():
                    self._file_hashes[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()[:16]

        logger.info(
            "sop watcher: reloaded %d projects hash=%s projects=%s",
            len(self.loaded_projects),
            self.sop_hash,
            self.loaded_projects,
        )
        return True

    def _reload_from_md(self) -> None:
        self.normalized_projects = load_normalized_project_sops(self.sop_dir)
        compiler_input = normalized_project_sops_to_compiler_input(self.normalized_projects)
        self.plan = SopMonitoringPlanCompiler().compile(compiler_input)
        self.loaded_projects = [p.project_id for p in self.normalized_projects]

    def _reload_from_yaml(self, yaml_files: list[Path]) -> None:
        """R27: Load YAML ProjectSOP files and convert to compiler input format."""
        project_sops: list[dict[str, Any]] = []
        self.normalized_projects = []

        for yaml_path in sorted(yaml_files):
            sop: ProjectSOP = load_project_sop(yaml_path)
            compiler_entry = _project_sop_yaml_to_compiler_input(sop, yaml_path)
            project_sops.append(compiler_entry)
            self.loaded_projects.append(sop.project_id)

        self.plan = SopMonitoringPlanCompiler().compile(project_sops)

    def to_status_dict(self) -> dict[str, Any]:
        # R27: detect whether source is git-tracked (config/project_sops/ is in-repo)
        is_git = "config/project_sops" in str(self.sop_dir)
        source_of_truth = "git" if is_git else "ungit-tracked"
        return {
            "loaded_projects": list(self.loaded_projects),
            "last_reload": self.last_reload,
            "sop_hash": self.sop_hash,
            "sop_dir": str(self.sop_dir.resolve()),
            "source_of_truth": source_of_truth,
            "file_hashes": dict(self._file_hashes),
        }


class SopWatcher:
    """Watches SOP directory for mtime changes and hot-swaps the runtime plan."""

    def __init__(self, sop_dir: str | Path):
        self.sop_dir = Path(sop_dir)
        self.runtime = SopRuntime(sop_dir=self.sop_dir)
        self._last_mtimes: dict[str, float] = {}
        self.runtime.reload()
        # Record initial mtimes for all SOP files (.yaml and .md)
        for pattern in ("*.yaml", "*.md"):
            for f in sorted(self.sop_dir.glob(pattern)):
                if f.is_file():
                    self._last_mtimes[f.name] = f.stat().st_mtime

    def check_and_reload(self) -> bool:
        """Check for mtime changes and reload if needed.

        Returns True if the plan was updated.
        """
        changed = False
        current_files = set()
        for pattern in ("*.yaml", "*.md"):
            for f in self.sop_dir.glob(pattern):
                if not f.is_file():
                    continue
                current_files.add(f.name)
                current_mtime = f.stat().st_mtime
                if f.name not in self._last_mtimes or current_mtime != self._last_mtimes[f.name]:
                    changed = True
                    self._last_mtimes[f.name] = current_mtime

        # Also detect file deletions
        removed = set(self._last_mtimes) - current_files
        if removed:
            changed = True
            for name in removed:
                del self._last_mtimes[name]

        if changed:
            return self.runtime.reload()
        return False

    @property
    def monitoring_plan(self) -> dict[str, Any]:
        """Return the current monitoring plan (live copy, hot-swappable)."""
        return self.runtime.plan

    @property
    def status(self) -> dict[str, Any]:
        """Return runtime status for --status output."""
        return self.runtime.to_status_dict()


# ── R27: YAML ProjectSOP → Compiler Input converter ─────────────────────


def _project_sop_yaml_to_compiler_input(
    sop: ProjectSOP,
    yaml_path: Path,
) -> dict[str, Any]:
    """Convert a YAML ProjectSOP to the format SopMonitoringPlanCompiler expects."""
    sop_nodes: list[dict[str, Any]] = []

    for task in sop.listening_tasks:
        monitoring_entries: list[dict[str, Any]] = []

        for rule in task.routing:
            entry: dict[str, Any] = {
                "channel": "wechat",
                "group_id": task.group_id,
            }
            if task.group_name:
                entry["group_name"] = task.group_name

            if rule.message_type == "image":
                entry["input_type"] = "image"
                # Extract document_type from trigger_condition if available
                cond = (rule.trigger_condition or "").strip()
                if cond.startswith("category_in:"):
                    cats = cond.replace("category_in:", "").strip("[]")
                    entry["document_type"] = cats
                elif cond:
                    entry["document_type"] = cond
            elif rule.message_type == "text":
                # R27: YAML SOPs define trigger_condition but not text_patterns.
                # Skip text watch-items that use generic templates — the fallback
                # alignment matcher handles keyword-based routing.
                # (e.g., 朝阳西/木森17 → chaoyang_steel via _fallback_alignment_match)
                if rule.trigger_condition in (
                    "match_departure_text_template",
                    "match_text_template",
                    "always",
                ):
                    continue  # skip — let fallback matcher handle it
                entry["input_type"] = "text"
                entry["message_type"] = rule.trigger_condition or ""
                # R32: propagate text_patterns from RoutingRule into compiler input
                if rule.text_patterns:
                    entry["text_patterns"] = list(rule.text_patterns)
            else:
                entry["input_type"] = rule.message_type or "*"

            entry["target_sop_node"] = (
                f"{sop.project_id}:{task.group_id}:{rule.target_node}"
            )
            monitoring_entries.append(entry)

        if monitoring_entries:
            sop_nodes.append({
                "node_id": f"{sop.project_id}:{task.group_id}:{task.group_name or task.group_id}",
                "monitoring": monitoring_entries,
            })

    return {
        "project_id": sop.project_id,
        "project_name": sop.project_name or yaml_path.stem,
        "sop_nodes": sop_nodes,
    }

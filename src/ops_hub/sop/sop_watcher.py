"""Live SOP Runtime Compiler.

Monitors SOP YAML files for changes. When a file changes:
1. Reloads all SOP fixtures
2. Runs Normalizer (load_normalized_project_sops)
3. Runs SopMonitoringPlanCompiler
4. Hot-swaps the runtime monitoring plan atomically

No restart required.

R26: Part B — SOPWatcher
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ops_hub.models.project_sop import NormalizedProjectSOP, load_normalized_project_sops
from ops_hub.sop.monitoring_plan_compiler import SopMonitoringPlanCompiler
from ops_hub.sop.monitoring_plan_preview import normalized_project_sops_to_compiler_input

logger = logging.getLogger("ops_hub.sop_watcher")


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

        Returns True if the plan changed (hash differs), False otherwise.
        """
        new_hash = self.compute_dir_hash()
        if new_hash and new_hash == self.sop_hash:
            return False

        self.normalized_projects = load_normalized_project_sops(self.sop_dir)
        compiler_input = normalized_project_sops_to_compiler_input(self.normalized_projects)
        self.plan = SopMonitoringPlanCompiler().compile(compiler_input)
        self.sop_hash = new_hash
        self.loaded_projects = [p.project_id for p in self.normalized_projects]
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

    def to_status_dict(self) -> dict[str, Any]:
        return {
            "loaded_projects": list(self.loaded_projects),
            "last_reload": self.last_reload,
            "sop_hash": self.sop_hash,
            "sop_dir": str(self.sop_dir.resolve()),
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

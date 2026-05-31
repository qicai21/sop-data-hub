"""Global storage policy loader and validator for sop-data-hub.

This module stays read-only:
- load the config/storage_policy.yaml contract;
- validate required fields and structural constraints;
- resolve document types and media-type policies;
- do NOT trigger file migration or cleanup.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


# ── Resolution helpers ─────────────────────────────────────────────────

def _project_root() -> Path:
    """Return the repo root (sop-data-hub directory)."""
    # __file__ = .../sop-data-hub/src/ops_hub/sop/storage_policy.py
    # parents[0] = sop/, parents[1] = ops_hub/, parents[2] = src/, parents[3] = sop-data-hub/
    return Path(__file__).resolve().parents[3]


def get_default_storage_policy_path() -> Path:
    """Default path: <repo_root>/config/storage_policy.yaml"""
    env = os.environ.get("SOP_STORAGE_POLICY_PATH")
    if env:
        return Path(env).expanduser()
    return _project_root() / "config" / "storage_policy.yaml"


# ── Core load / validate ───────────────────────────────────────────────

def load_storage_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load the storage policy YAML file into a dict."""
    target = Path(path) if path else get_default_storage_policy_path()
    if not target.exists():
        raise FileNotFoundError(f"storage_policy.yaml not found at {target}")
    with target.open("r", encoding="utf-8") as f:
        policy = yaml.safe_load(f)
    if not isinstance(policy, dict):
        raise ValueError("storage_policy.yaml root must be a dict")
    return policy


def validate_storage_policy(policy: dict[str, Any]) -> list[str]:
    """Validate structural constraints. Returns list of error strings (empty = valid)."""
    errors: list[str] = []

    # version
    if policy.get("version") != 1:
        errors.append("version must be 1")

    # asset_root
    ar = policy.get("asset_root")
    if not ar or not isinstance(ar, str):
        errors.append("asset_root must be a non-empty string")

    # raw_standard_image
    rsi = policy.get("raw_standard_image", {})
    if not isinstance(rsi, dict):
        errors.append("raw_standard_image must be a dict")
    elif not rsi.get("path_pattern"):
        errors.append("raw_standard_image.path_pattern required")

    # canonical_documents must contain 出港计划通知单 and 检装车通知单
    cd = policy.get("canonical_documents", [])
    if not isinstance(cd, list):
        errors.append("canonical_documents must be a list")
    else:
        cd_types = {d.get("document_type") for d in cd if isinstance(d, dict)}
        for req in ("出港计划通知单", "检装车通知单"):
            if req not in cd_types:
                errors.append(f"canonical_documents missing required type: {req}")

    # media_types
    mt = policy.get("media_types", {})
    if not isinstance(mt, dict):
        errors.append("media_types must be a dict")
    else:
        for req_media in ("image", "video", "file"):
            if req_media not in mt:
                errors.append(f"media_types missing: {req_media}")

    # manifest.required_fields non-empty
    mf = (policy.get("manifest") or {}).get("required_fields", [])
    if not isinstance(mf, list) or len(mf) == 0:
        errors.append("manifest.required_fields must be a non-empty list")

    return errors


# ── Domain helpers ──────────────────────────────────────────────────────

def resolve_document_stage(policy: dict[str, Any], document_type: str) -> str | None:
    """Return the document_stage for a canonical document type, or None."""
    for doc in policy.get("canonical_documents", []):
        if isinstance(doc, dict) and doc.get("document_type") == document_type:
            return doc.get("document_stage")
    return None


def is_canonical_document(policy: dict[str, Any], document_type: str) -> bool:
    """Check whether a document_type is a canonical business document."""
    for doc in policy.get("canonical_documents", []):
        if isinstance(doc, dict) and doc.get("document_type") == document_type:
            return True
    return False


def get_media_type_policy(policy: dict[str, Any], media_type: str) -> dict[str, Any]:
    """Return the default media-type policy dict, or an empty dict."""
    mt = policy.get("media_types", {})
    return mt.get(media_type, {}) if isinstance(mt, dict) else {}


def is_general_document(policy: dict[str, Any], document_type: str) -> bool:
    """Check whether a document_type is listed as a general (non-canonical) document."""
    for doc in policy.get("general_documents", []):
        if isinstance(doc, dict) and doc.get("document_type") == document_type:
            return True
    return False


# ── CLI entry point ─────────────────────────────────────────────────────

def validate_cli(path: str | Path | None = None) -> dict[str, Any]:
    """Validate storage policy and return a JSON-compatible result."""
    policy_path = Path(path) if path else get_default_storage_policy_path()
    result: dict[str, Any] = {
        "ok": False,
        "policy_path": str(policy_path),
    }

    try:
        policy = load_storage_policy(policy_path)
    except Exception as exc:
        result["errors"] = [str(exc)]
        return result

    errors = validate_storage_policy(policy)
    result["ok"] = len(errors) == 0
    result["asset_root"] = policy.get("asset_root", "")
    result["canonical_documents"] = [
        d.get("document_type") for d in policy.get("canonical_documents", []) if isinstance(d, dict)
    ]
    result["media_types"] = {
        k: v.get("default") for k, v in policy.get("media_types", {}).items() if isinstance(v, dict)
    }
    result["has_project_override_contract"] = "project_override_contract" in policy
    if errors:
        result["errors"] = errors

    return result


if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser(description="Validate sop-data-hub storage policy")
    p.add_argument("--validate", action="store_true", help="Load and validate storage_policy.yaml")
    p.add_argument("--path", type=str, default=None, help="Override path to storage_policy.yaml")
    args = p.parse_args()

    if args.validate:
        print(json.dumps(validate_cli(args.path), ensure_ascii=False, indent=2))
    else:
        # Default: print policy summary
        policy = load_storage_policy(args.path)
        print(f"version:   {policy.get('version')}")
        print(f"asset_root: {policy.get('asset_root')}")
        print(f"canonical_documents: {len(policy.get('canonical_documents', []))}")
        print(f"general_documents:   {len(policy.get('general_documents', []))}")
        print(f"media_types:          {list(policy.get('media_types', {}).keys())}")
        print(f"manifest_fields:      {len((policy.get('manifest') or {}).get('required_fields', []))}")
        print(f"has_project_override: {'project_override_contract' in policy}")

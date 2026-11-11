"""S2: split spec.md by heading and lint it. No model is involved anywhere."""
from .lint import LintResult, lint_spec
from .split import Resource, SpecParts, split_spec

__all__ = ["LintResult", "Resource", "SpecParts", "lint_spec", "split_spec"]

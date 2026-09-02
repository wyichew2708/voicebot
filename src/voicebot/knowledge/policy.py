"""How a deployment is allowed to use the bundle.

One setting carries most of the weight. `unsourced_answers` decides whether a
draft page -- wording with no ingested source behind it -- may be spoken.

  allow    the demo. Placeholder coverage wording is spoken, as it was before
           the knowledge layer existed. Nothing regresses, and the console
           behaves the way it did yesterday.
  refuse   anything heading for a real customer. A coverage question with no
           source becomes a callback from a colleague, which is the honest
           answer while the product this bot sells has no policy wording in
           the corpus.

The bundle ships `refuse`. A deployment opts into `allow` explicitly, in its
own config file, where someone can see it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .okf import Bundle


@dataclass(frozen=True)
class Serving:
    jurisdiction: str = "SG"
    products: tuple[str, ...] = ()
    allow_unsourced: bool = False

    @property
    def describe(self) -> str:
        scope = ", ".join(self.products) or "all products"
        return (f"{self.jurisdiction} / {scope} / "
                f"unsourced answers "
                f"{'allowed' if self.allow_unsourced else 'refused'}")


def _as_bool(value: Any, *, field: str) -> bool:
    """`unsourced_answers` is a word, not a flag, because `false` in a config
    file does not say what it is refusing."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("allow", "allowed", "yes", "true"):
        return True
    if text in ("refuse", "refused", "no", "false"):
        return False
    raise ValueError(f"{field}: expected allow or refuse, got {value!r}")


def resolve(cfg: dict[str, Any] | None = None,
            bundle: Bundle | None = None) -> Serving:
    """Bundle defaults, overridden by the deployment's own `knowledge:` block."""
    if bundle is None:
        from .answer import default_bundle
        bundle = default_bundle()
    base = dict(bundle.manifest.get("serving") or {})
    base.update((cfg or {}).get("knowledge") or {})

    return Serving(
        jurisdiction=str(base.get("jurisdiction", "SG")),
        products=tuple(str(p) for p in base.get("products", []) or ()),
        allow_unsourced=_as_bool(base.get("unsourced_answers", "refuse"),
                                 field="unsourced_answers"),
    )


_default: Serving | None = None


def configure(cfg: dict[str, Any] | None = None) -> Serving:
    """Called once at startup. Returns what it set, for logging."""
    global _default
    _default = resolve(cfg)
    return _default


def default_serving() -> Serving:
    global _default
    if _default is None:
        _default = resolve(None)
    return _default

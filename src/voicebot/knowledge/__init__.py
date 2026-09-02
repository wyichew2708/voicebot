"""The knowledge layer: an OKF bundle, and a deterministic reader for it.

The bot's safety property is that no model ever writes a spoken word. This
package does not weaken it. A wiki page carries pre-approved `spoken` wording;
retrieval selects a page by frontmatter filter and alias match, both of which
are ordinary code; and if nothing matches, the answer is a colleague, not a
guess. Nothing here generates prose.

See `docs/knowledge-layer.md` and `knowledge/okf.yaml`.
"""
from .okf import Bundle, BundleError, Page, load_bundle      # noqa: F401
from .answer import Answer, lookup, spoken_lines             # noqa: F401
from .policy import Serving, configure, default_serving, resolve  # noqa: F401

__all__ = ["Bundle", "BundleError", "Page", "load_bundle",
           "Answer", "lookup", "spoken_lines",
           "Serving", "configure", "default_serving", "resolve"]

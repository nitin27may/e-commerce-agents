"""Full-text and hybrid retrieval helpers shared by the product tools.

The catalog carries a weighted ``products.search_vector`` tsvector (name=A,
brand=B, description=C) with a GIN index. These helpers build the SQL
fragments that query it consistently across agents.

Note: ``packages/mcp-product`` vendors its own copy of these fragments rather
than importing this module — it is an isolated uv workspace member that must
stay installable without the ``shared`` library.
"""

from __future__ import annotations

# Reciprocal Rank Fusion constant. 60 is the value from the original RRF paper
# (Cormack et al. 2009) and the de-facto default in hybrid-search implementations:
# large enough that the top few ranks score close together, so a document found by
# both arms beats one that merely ranks first in a single arm.
RRF_K = 60


def or_joined_tsquery(param: str) -> str:
    """SQL expression turning a text parameter into an OR-joined tsquery.

    ``plainto_tsquery`` ANDs its lexemes, so "noise cancelling headphones"
    becomes ``'nois' & 'cancel' & 'headphon'`` and matches only products
    containing all three — the same all-terms-required behavior as the
    per-word ILIKE loop this replaced, which is what made those queries
    return nothing. Rewriting the operators to ``|`` makes any term a match
    and leaves ``ts_rank`` to sort full matches above partial ones.

    Args:
        param: Positional parameter placeholder, e.g. ``"$1"``.

    Returns:
        A SQL expression of type ``tsquery``.
    """
    return f"replace(plainto_tsquery('english', {param})::text, '&', '|')::tsquery"

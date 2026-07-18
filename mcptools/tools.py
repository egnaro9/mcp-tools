"""The tools this server exposes — both safe by construction.

- `calc` evaluates arithmetic without `eval()`: it walks a parsed AST and
  permits arithmetic nodes only, so a model that emits `__import__("os")` gets a
  rejection, not a shell. That's the OWASP LLM06 (Excessive Agency) mitigation —
  a tool that can do arithmetic and *nothing else*.
- `search` is BM25 over a small bundled corpus: the same length-normalised,
  saturation-aware ranking that matches the published SciFact baseline in
  rag-eval-lab, reimplemented here so this server has zero dependencies.

Nothing here reaches the network or the filesystem; the whole server is stdlib.
"""
from __future__ import annotations

import ast
import json
import math
import operator as op
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

# ───────────────────────── calc: AST allow-list ──────────────────────────
_BINOPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
           ast.Pow: op.pow, ast.Mod: op.mod, ast.FloorDiv: op.floordiv}
_UNARY = {ast.UAdd: op.pos, ast.USub: op.neg}


class ToolError(ValueError):
    """A tool-execution error — reported to the client, never a crash."""


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    # A Call, Name, Attribute, or anything else never reaches evaluation.
    raise ToolError(f"unsafe or unsupported expression element: {type(node).__name__}")


def calc(expression: str) -> str:
    """Evaluate an arithmetic expression. Rejects anything that isn't arithmetic."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"not a valid expression: {expression!r}") from e
    result = _eval(tree)
    return str(int(result) if result == int(result) else result)


# ───────────────────────────── search: BM25 ──────────────────────────────
_TOKEN = re.compile(r"[a-z0-9]+")


def _tok(text: str) -> List[str]:
    return _TOKEN.findall(text.lower())


class BM25:
    """Okapi BM25 over a set of documents. Same algorithm as rag-eval-lab."""

    def __init__(self, docs: Dict[str, str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.ids = list(docs)
        self.texts = docs
        self._toks = [_tok(t) for t in docs.values()]
        self._tf = [Counter(d) for d in self._toks]
        n = len(self._toks) or 1
        self._avg = sum(len(d) for d in self._toks) / n
        df: Counter = Counter()
        self._post: Dict[str, List[int]] = defaultdict(list)
        for i, toks in enumerate(self._toks):
            for t in set(toks):
                df[t] += 1
                self._post[t].append(i)
        self._idf = {t: max(0.0, math.log((n - c + 0.5) / (c + 0.5) + 1.0)) for t, c in df.items()}

    def search(self, query: str, k: int = 3) -> List[Tuple[str, str, float]]:
        q = [t for t in _tok(query) if t in self._idf]
        if not q:
            return []
        cands = set()
        for t in q:
            cands.update(self._post.get(t, ()))
        scored = []
        for i in cands:
            tf, dl = self._tf[i], len(self._toks[i])
            norm = self.k1 * (1 - self.b + self.b * dl / (self._avg or 1))
            s = sum(self._idf[t] * (tf[t] * (self.k1 + 1)) / (tf[t] + norm) for t in q if tf.get(t))
            if s > 0:
                scored.append((self.ids[i], self.texts[self.ids[i]], s))
        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[:k]


def _load_corpus() -> Dict[str, str]:
    """Bundled default corpus, overridable with MCPTOOLS_CORPUS=/path/to.json.

    The file is `{ "doc-id": "text", ... }` — point it at your own notes and the
    `search` tool searches those instead. Kept small and swappable on purpose.
    """
    override = os.environ.get("MCPTOOLS_CORPUS")
    if override:
        return json.loads(Path(override).read_text(encoding="utf-8"))
    return json.loads((Path(__file__).parent / "corpus.json").read_text(encoding="utf-8"))


_CORPUS = _load_corpus()
_BM25 = BM25(_CORPUS)


def search(query: str, k: int = 3) -> str:
    hits = _BM25.search(query, k=k)
    if not hits:
        return f"No results for {query!r} in the {len(_CORPUS)}-document corpus."
    lines = [f"{len(hits)} result(s) for {query!r}:"]
    for doc_id, text, score in hits:
        lines.append(f"\n[{doc_id}] (score {score:.2f})\n{text}")
    return "\n".join(lines)

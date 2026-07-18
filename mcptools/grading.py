"""Grade an answer against its sources — deterministically, with no LLM judge.

This is the tool that makes the eval stack *useful to an agent* rather than a
demo of itself: an agent can call it on its own draft answer and find out which
sentences its sources don't actually support, before it sends them.

Why not ask a model? Because then a "hallucination" verdict is itself a model
output — you can't tell a real unsupported claim from the judge having a bad
day, and you can't reproduce last week's score. Everything here is lexical and
deterministic: the same answer and sources always produce the same verdict.

Two signals, in order of how much they mean:

1. **Fabricated numbers.** A figure, percentage, or year in the answer that
   appears nowhere in the sources is the single strongest hallucination tell —
   models invent precise-sounding statistics. Any unsupported number fails the
   sentence outright, regardless of how well the rest of it overlaps.
2. **Content coverage.** The fraction of a sentence's meaningful words (minus
   stopwords) that actually occur in the sources. Below the threshold, the
   sentence is asserting something the sources don't discuss.

The result names the offending sentences, because "faithfulness: 0.67" tells you
nothing you can act on and "this sentence isn't in your sources" tells you what
to cut.
"""
from __future__ import annotations

import re
from typing import Dict, List, Sequence

# Small, deliberately boring stopword list — words whose presence says nothing
# about whether a claim is supported.
_STOP = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those",
    "is", "are", "was", "were", "be", "been", "being", "am", "it", "its", "as", "at", "by", "for",
    "from", "in", "into", "of", "on", "onto", "to", "with", "without", "about", "over", "under",
    "can", "could", "will", "would", "should", "may", "might", "must", "do", "does", "did", "done",
    "have", "has", "had", "having", "not", "no", "so", "such", "there", "here", "which", "who",
    "whom", "whose", "what", "when", "where", "why", "how", "all", "any", "both", "each", "more",
    "most", "other", "some", "only", "own", "same", "also", "very", "you", "your", "they", "them",
    "their", "we", "our", "i", "he", "she", "his", "her", "him", "us", "me", "my",
    # Bare affirmations carry no lexically checkable content. Flagging "Yes." as an
    # unsupported claim is a false positive with nothing to act on, and false
    # positives are what make a checker get ignored — so they're skipped. The
    # tradeoff is honest: an unsupported bare yes/no slips through. Any sentence
    # with real content ("Yes, revenue grew 47%") is still checked in full.
    "yes", "true", "false", "correct", "incorrect", "maybe", "perhaps", "sure", "ok", "okay",
}

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")
# Numbers, percentages and years — the things models most often invent.
_NUMBER = re.compile(r"\d+(?:\.\d+)?%?")


def _content_words(text: str) -> List[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and not w.isdigit()]


def _numbers(text: str) -> List[str]:
    return [n.rstrip("%") for n in _NUMBER.findall(text)]


def sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]


def grade_answer(answer: str, sources: Sequence[str], threshold: float = 0.6) -> Dict:
    """Check each sentence of `answer` against `sources`.

    Returns the faithfulness score, the per-sentence detail, and — the useful
    part — the specific sentences the sources don't support.
    """
    if not answer or not answer.strip():
        return {"faithfulness": 0.0, "n_claims": 0, "supported": 0,
                "unsupported": [], "verdict": "empty answer", "claims": []}
    if not sources or not any(s.strip() for s in sources):
        return {"faithfulness": 0.0, "n_claims": 0, "supported": 0, "unsupported": [],
                "verdict": "no sources given — nothing to check against", "claims": []}

    src_text = " ".join(sources)
    src_words = set(_content_words(src_text))
    src_numbers = set(_numbers(src_text))

    claims: List[Dict] = []
    for sent in sentences(answer):
        words = _content_words(sent)
        if not words:                      # e.g. "Yes." — no claim to check
            continue
        hits = [w for w in words if w in src_words]
        coverage = len(hits) / len(words)
        invented = sorted({n for n in _numbers(sent) if n not in src_numbers})
        supported = coverage >= threshold and not invented
        reason = ("supported" if supported
                  else f"figure(s) not in sources: {', '.join(invented)}" if invented
                  else f"only {coverage:.0%} of its content words appear in the sources")
        claims.append({"sentence": sent, "coverage": round(coverage, 3),
                       "unsupported_numbers": invented, "supported": supported, "reason": reason})

    n = len(claims)
    ok = sum(1 for c in claims if c["supported"])
    unsupported = [c for c in claims if not c["supported"]]
    faithfulness = round(ok / n, 4) if n else 0.0
    verdict = ("fully supported" if n and not unsupported
               else "no checkable claims" if not n
               else f"{len(unsupported)} of {n} claim(s) not supported by the sources")
    return {"faithfulness": faithfulness, "n_claims": n, "supported": ok,
            "unsupported": unsupported, "verdict": verdict, "claims": claims}


def format_report(result: Dict) -> str:
    """Render the grade for a model to read — verdict first, then what to fix."""
    lines = [f"faithfulness {result['faithfulness']:.0%} — {result['verdict']}"]
    if result["unsupported"]:
        lines.append("\nClaims your sources do not support:")
        for c in result["unsupported"]:
            lines.append(f"  • {c['sentence']}\n    ↳ {c['reason']}")
        lines.append("\nCut these, or cite a source that backs them.")
    elif result["n_claims"]:
        lines.append("Every sentence is grounded in the sources provided.")
    return "\n".join(lines)

"""Shared text processing utilities.

One canonical stopword set. One Jaccard function. One word extractor.
Used by sieve, projections, atlas, and review_perception.

Pure. No I/O.
"""

from __future__ import annotations

import re
import string


STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "for", "of", "in", "on", "and", "or", "but", "not", "with",
    "at", "by", "from", "as", "into", "through", "about", "between",
    "it", "its", "this", "that", "these", "those", "he", "she", "they",
    "we", "you", "i", "my", "your", "our", "their", "his", "her",
    "do", "does", "did", "will", "would", "could", "should", "can",
    "may", "might", "shall", "has", "have", "had",
    "so", "if", "then", "than", "no", "yes", "all", "some", "any",
    "each", "every", "both", "few", "more", "most", "other", "such",
    "what", "which", "who", "whom", "how", "when", "where", "why",
    "up", "out", "off", "over", "under", "again", "once", "here", "there",
    "just", "also", "very", "too", "quite", "really", "only", "now",
    "about", "into", "been", "being",
})

# Speaker names and common thread filler words (not meaningful for topic extraction)
FILLER_WORDS = frozenset({
    "dev", "lead", "eng", "alice", "bob", "sarah", "tom", "pm",
    "think", "know", "want", "need", "get", "got", "let",
    "said", "say", "says", "look", "looks", "make", "take",
    "come", "going", "wait", "sure", "fine", "okay", "yeah",
    "good", "right", "like", "thing", "things", "way",
})

SPEAKER_RE = re.compile(r"^[A-Z][A-Za-z\s]*:\s*")


def extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text. Pure.

    Strips stopwords, keeps compound terms via bigrams.
    """
    stripped = text.lower().translate(str.maketrans("", "", string.punctuation))
    words = set(stripped.split()) - STOPWORDS

    # Bigrams for compound concepts
    raw_words = stripped.split()
    for i in range(len(raw_words) - 1):
        a, b = raw_words[i], raw_words[i + 1]
        if a not in STOPWORDS and b not in STOPWORDS:
            words.add(f"{a} {b}")

    return words


def extract_topic_words(text: str) -> set[str]:
    """Extract topic-relevant words, excluding stopwords AND filler. Pure."""
    stripped = text.lower().translate(str.maketrans("", "", string.punctuation))
    return set(stripped.split()) - STOPWORDS - FILLER_WORDS


def normalize_words(text: str) -> set[str]:
    """Normalize text to word set for dedup comparison. Pure."""
    stripped = text.lower().translate(str.maketrans("", "", string.punctuation))
    return set(stripped.split())


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two word sets. Pure."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def strip_speaker(text: str) -> str:
    """Strip speaker attribution from 'Speaker: text' format. Pure."""
    return SPEAKER_RE.sub("", text).strip()


def extract_speaker(text: str) -> str:
    """Extract speaker name from 'Speaker: text' format. Pure."""
    m = SPEAKER_RE.match(text)
    if m:
        return m.group().rstrip(": ").strip()
    return ""

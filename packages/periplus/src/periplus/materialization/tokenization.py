"""Pinned ICU search-term semantics, shared by materialization and its bench."""
from collections import Counter

import icu

TOKENIZER_POLICY = 'icu-word-nfc-casefold-v1'
TOKENIZER_VERSIONS = ('2.16.2', '77.1', '16.0')


def validate_tokenizer() -> None:
    actual = (icu.VERSION, icu.ICU_VERSION, icu.UNICODE_VERSION)
    if actual != TOKENIZER_VERSIONS:
        raise RuntimeError(f'term tokenizer requires PyICU/ICU/Unicode {TOKENIZER_VERSIONS}, got {actual}')


def term_tokens(text: str) -> list[str]:
    validate_tokenizer()
    folded = icu.UnicodeString(text)
    folded.foldCase()
    normalized = icu.UnicodeString(icu.Normalizer2.getNFCInstance().normalize(folded))
    iterator = icu.BreakIterator.createWordInstance(icu.Locale.getRoot())
    iterator.setText(normalized)
    start = iterator.first()
    tokens = []
    for end in iterator:
        # ICU offsets are UTF-16 code units; slicing Python str corrupts astral text.
        if iterator.getRuleStatus() >= 100:
            tokens.append(str(normalized[start:end]))
        start = end
    return tokens


def term_counts(text: str) -> Counter[str]:
    return Counter(term_tokens(text))


def tokenizer_metadata() -> dict[str, str]:
    return {'policy': TOKENIZER_POLICY, 'locale': 'root', 'pyicu': icu.VERSION,
            'icu': icu.ICU_VERSION, 'unicode': icu.UNICODE_VERSION}

from __future__ import annotations

import pytest

from gideon.engines.stt_faster_whisper import Decoding, pick_best


def test_stt_keeps_the_most_confident_non_empty_decoding() -> None:
    # Real numbers from the "Notlarımı oku." fixture decoded with whisper-base in both languages.
    en = Decoding("en", "Not very much.", -1.06)
    tr = Decoding("tr", "Notlarımı oku.", -0.16)
    assert pick_best([en, tr]) is tr
    assert pick_best([Decoding("en", "", 0.0), Decoding("tr", "saat kaç", -2.0)]).language == "tr"


def test_stt_needs_at_least_one_decoding() -> None:
    with pytest.raises(ValueError, match="no decodings"):
        pick_best([])

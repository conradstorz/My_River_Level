from monitor.search_text import _tokenize, _score, _STATE_ABBR


def test_tokenize_uppercases_and_drops_short_tokens():
    assert _tokenize("Ohio River at Louisville, KY") == [
        "OHIO", "RIVER", "AT", "LOUISVILLE", "KY"]
    assert _tokenize("a b cd") == ["CD"]


def test_score_exact_tokens_sum_to_one_each():
    assert _score("OHIO RIVER AT LOUISVILLE, KY", ["OHIO", "RIVER"]) == 2.0


def test_score_credits_state_name_via_abbreviation():
    assert _score("OHIO RIVER AT LOUISVILLE, KY", ["KENTUCKY"]) == 1.0
    assert _STATE_ABBR["KENTUCKY"] == "KY"


def test_score_ranks_typo_nearest_word_highest():
    tokens = ["LOUSVILLE", "OHIO", "RIVER"]
    assert (_score("OHIO RIVER AT LOUISVILLE, KY", tokens)
            > _score("OHIO RIVER AT CINCINNATI, OH", tokens))

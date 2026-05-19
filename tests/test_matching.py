from app.services.matching import MatchInput, score


def _cand(**overrides):
    base = {
        "id": "x", "primary_name": "Baiterek Cafe", "brand_name": None,
        "phone_number": "+7-7172-100-2000",
        "primary_website_url": "https://baiterekcafe.kz",
        "formatted_address": "Kabanbay Batyr 10, Astana, Kazakhstan",
        "meters": 50.0, "category_code": "cafe",
    }
    base.update(overrides)
    return base


def test_name_exact_and_close_distance_high_score():
    s, b = score(
        MatchInput(name="Baiterek Cafe", address="Kabanbay Batyr 10",
                   lat=51.13, lng=71.43, category="cafe"),
        _cand(),
    )
    assert s >= 0.85
    assert b["name"] >= 0.9


def test_far_distance_drags_score_down():
    s_close, _ = score(MatchInput(name="Baiterek Cafe", lat=51.13, lng=71.43), _cand(meters=20.0))
    s_far, _ = score(MatchInput(name="Baiterek Cafe", lat=51.13, lng=71.43), _cand(meters=2000.0))
    assert s_close > s_far


def test_name_mismatch_yields_low_score():
    s, _ = score(MatchInput(name="Totally Unrelated Tacos", lat=51.13, lng=71.43), _cand())
    assert s < 0.6


def test_name_local_matches_cross_script():
    # Candidate's primary_name is Latin, name_local is Cyrillic.
    # Customer sends the Cyrillic form — _name_score should max() across
    # primary/brand/local and score high.
    s_cyrillic, b_cyrillic = score(
        MatchInput(name="Кофе Лайк", lat=51.13, lng=71.43),
        _cand(primary_name="Coffee Like", name_local="Кофе Лайк", meters=20.0),
    )
    s_no_local, _ = score(
        MatchInput(name="Кофе Лайк", lat=51.13, lng=71.43),
        _cand(primary_name="Coffee Like", name_local=None, meters=20.0),
    )
    assert b_cyrillic["name"] >= 0.85
    assert s_cyrillic > s_no_local


def test_wrong_phone_drags_score_down():
    s_match, _ = score(
        MatchInput(name="Baiterek Cafe", phone="+7-7172-100-2000", lat=51.13, lng=71.43),
        _cand(),
    )
    s_mismatch, _ = score(
        MatchInput(name="Baiterek Cafe", phone="+7-7172-999-9999", lat=51.13, lng=71.43),
        _cand(),
    )
    assert s_match > s_mismatch

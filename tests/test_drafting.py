from saafroute.drafting import FallbackDrafter, template_draft
from saafroute.schemas import TicketDraft

FACTS = {"sector": 22, "kind": "overflowing_bin", "waste_type": "mixed", "severity": 3, "reason": "Bags around a bin."}


class Boom:
    name = "boom"

    def draft(self, lang, facts):
        raise RuntimeError("quota exhausted")


class Fine:
    name = "fine"

    def draft(self, lang, facts):
        return TicketDraft(title="Bin in Sector 22", body="A full bin in Sector 22 needs collection.")


def test_template_all_languages():
    assert "Sector 22" in template_draft("en", FACTS).title
    assert "सेक्टर 22" in template_draft("hi", FACTS).title
    assert "ਸੈਕਟਰ 22" in template_draft("pa", FACTS).title


def test_template_never_mentions_score():
    for lang in ("en", "hi", "pa"):
        assert "score" not in template_draft(lang, FACTS).body.lower()


def test_fallback_to_template_on_model_failure():
    d = FallbackDrafter(Boom())
    out = d.draft("hi", FACTS)
    assert d.last_used == "template" and "सेक्टर 22" in out.title


def test_primary_used_when_it_works():
    d = FallbackDrafter(Fine())
    assert d.draft("en", FACTS).title == "Bin in Sector 22" and d.last_used == "fine"


def test_secondary_model_used_when_primary_fails():
    d = FallbackDrafter(Boom(), Fine())
    assert d.draft("en", FACTS).title == "Bin in Sector 22" and d.last_used == "fine"


def test_template_when_both_models_fail():
    d = FallbackDrafter(Boom(), Boom())
    assert d.draft("en", FACTS).title and d.last_used == "template"


def test_groq_drafter_parses_json_and_retries_once(monkeypatch):
    from saafroute.drafting import GroqDrafter

    d = GroqDrafter("m", "key", 5)
    answers = iter(["not json", '{"title": "Bin in Sector 22", "body": "A full bin needs collection."}'])
    monkeypatch.setattr(d.client, "chat", lambda *a, **k: next(answers))
    assert d.draft("en", FACTS).title == "Bin in Sector 22"


def test_gemini_failure_is_logged_and_falls_back(caplog):
    from saafroute.drafting import FallbackDrafter

    class Bad:
        name = "bad"

        def draft(self, lang, facts):
            raise RuntimeError("API key not valid")

    facts = {"sector": 22, "kind": "litter", "waste_type": "mixed", "severity": 2, "reason": "x"}
    d = FallbackDrafter(Bad())
    with caplog.at_level("WARNING"):
        out = d.draft("en", facts)
    assert out.title and d.last_used == "template"
    assert "API key not valid" in caplog.text

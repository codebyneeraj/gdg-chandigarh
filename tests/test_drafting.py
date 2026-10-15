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

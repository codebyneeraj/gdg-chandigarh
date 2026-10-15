"""Ticket drafting in English, Hindi or Punjabi.

Gemini writes the draft from the validated photo analysis (never from the raw
photo, which is not sent to the drafting model). Output is schema-validated;
on failure we retry once and then fall back to fixed templates, so a ticket
always exists for the official to review. Nothing is dispatched without approval.
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import ValidationError

from .config import DraftingConfig
from .schemas import Lang, TicketDraft

LANG_NAMES = {"en": "English", "hi": "Hindi (Devanagari script)", "pa": "Punjabi (Gurmukhi script)"}

KIND = {
    "en": {"overflowing_bin": "Overflowing bin", "illegal_dump": "Illegal dumping", "litter": "Scattered litter",
           "clean": "No waste visible"},
    "hi": {"overflowing_bin": "भरा हुआ कूड़ादान", "illegal_dump": "अवैध कचरा ढेर", "litter": "बिखरा हुआ कचरा",
           "clean": "कोई कचरा नहीं"},
    "pa": {"overflowing_bin": "ਭਰਿਆ ਹੋਇਆ ਕੂੜੇਦਾਨ", "illegal_dump": "ਗੈਰ-ਕਾਨੂੰਨੀ ਕੂੜੇ ਦਾ ਢੇਰ", "litter": "ਖਿੱਲਰਿਆ ਕੂੜਾ",
           "clean": "ਕੋਈ ਕੂੜਾ ਨਹੀਂ"},
}
WASTE = {
    "en": {"mixed": "mixed waste", "dry_recyclable": "dry / recyclable waste", "wet_organic": "wet / organic waste",
           "construction_debris": "construction debris", "bulky": "bulky items", "e_waste": "e-waste",
           "hazardous": "hazardous waste", "none": "none"},
    "hi": {"mixed": "मिश्रित कचरा", "dry_recyclable": "सूखा / पुनर्चक्रण योग्य कचरा", "wet_organic": "गीला / जैविक कचरा",
           "construction_debris": "निर्माण मलबा", "bulky": "भारी सामान", "e_waste": "ई-कचरा",
           "hazardous": "खतरनाक कचरा", "none": "कोई नहीं"},
    "pa": {"mixed": "ਮਿਲਿਆ-ਜੁਲਿਆ ਕੂੜਾ", "dry_recyclable": "ਸੁੱਕਾ / ਰੀਸਾਈਕਲ ਹੋਣ ਯੋਗ ਕੂੜਾ", "wet_organic": "ਗਿੱਲਾ / ਜੈਵਿਕ ਕੂੜਾ",
           "construction_debris": "ਉਸਾਰੀ ਦਾ ਮਲਬਾ", "bulky": "ਵੱਡਾ ਸਮਾਨ", "e_waste": "ਈ-ਕੂੜਾ",
           "hazardous": "ਖ਼ਤਰਨਾਕ ਕੂੜਾ", "none": "ਕੋਈ ਨਹੀਂ"},
}
TEMPLATE = {
    "en": ("{kind} in Sector {sector}",
           "Citizen report from Sector {sector}: {kind_l} ({waste}), severity {severity}/5. "
           "Observed: {reason} Please schedule collection. Hotspot score {score}/100."),
    "hi": ("सेक्टर {sector}: {kind}",
           "सेक्टर {sector} से नागरिक रिपोर्ट: {kind} ({waste}), गंभीरता {severity}/5। "
           "कृपया कचरा उठाने की व्यवस्था करें। हॉटस्पॉट स्कोर: {score}/100।"),
    "pa": ("ਸੈਕਟਰ {sector}: {kind}",
           "ਸੈਕਟਰ {sector} ਤੋਂ ਨਾਗਰਿਕ ਰਿਪੋਰਟ: {kind} ({waste}), ਗੰਭੀਰਤਾ {severity}/5। "
           "ਕਿਰਪਾ ਕਰਕੇ ਕੂੜਾ ਚੁੱਕਣ ਦਾ ਪ੍ਰਬੰਧ ਕਰੋ। ਹੌਟਸਪੌਟ ਸਕੋਰ: {score}/100।"),
}


class Drafter(Protocol):
    name: str

    def draft(self, lang: Lang, facts: dict) -> TicketDraft: ...


def template_draft(lang: Lang, facts: dict) -> TicketDraft:
    kind = KIND[lang].get(facts.get("kind") or "", "Report needing review")
    title_t, body_t = TEMPLATE[lang]
    return TicketDraft(
        title=title_t.format(kind=kind, sector=facts["sector"])[:120],
        body=body_t.format(kind=kind, kind_l=kind.lower(), sector=facts["sector"],
                           waste=WASTE[lang].get(facts.get("waste_type") or "", "?"),
                           severity=facts.get("severity") or "?", score=facts.get("score", "?"),
                           reason=(facts.get("reason") or "").rstrip(".") + ".")[:1200],
    )


class TemplateDrafter:
    name = "template"

    def draft(self, lang: Lang, facts: dict) -> TicketDraft:
        return template_draft(lang, facts)


DRAFT_PROMPT = """You draft work tickets for a municipal solid-waste team in Chandigarh, India.
Write the ticket in {lang_name}. Use only the facts below; do not invent addresses, names, dates or numbers.
Facts (JSON): {facts}
Return JSON with:
- "title": at most 10 words, naming the problem and the sector
- "body": 2-4 short sentences for the field supervisor: what was reported, where (sector), how severe,
  what kind of vehicle/crew is likely needed (e.g. a tipper for construction debris, a regular collection
  vehicle for household waste, a separate pickup for e-waste or hazardous waste), and the hotspot score.
Keep it plain and polite. This is a DRAFT that a human official will review before anything is dispatched."""


class GeminiDrafter:
    def __init__(self, model: str, api_key: str, timeout_s: float) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self.name = f"gemini-api:{model}"
        self.model = model
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))

    def draft(self, lang: Lang, facts: dict) -> TicketDraft:
        prompt = DRAFT_PROMPT.format(lang_name=LANG_NAMES[lang], facts=json.dumps(facts, ensure_ascii=False))
        last: Exception | None = None
        for _ in range(2):
            try:
                resp = self.client.models.generate_content(
                    model=self.model, contents=prompt,
                    config=self._types.GenerateContentConfig(
                        temperature=0.2, response_mime_type="application/json", response_schema=TicketDraft),
                )
                return TicketDraft.model_validate_json(resp.text or "")
            except (ValidationError, ValueError) as exc:
                last = exc
                prompt += f"\nYour previous answer was invalid ({str(exc)[:200]}). Return only valid JSON."
            except Exception as exc:  # network / quota errors
                last = exc
        raise RuntimeError(f"drafting failed: {last}")


class FallbackDrafter:
    """Try Gemini; on any failure use the template so a ticket always exists."""

    def __init__(self, primary: Drafter | None) -> None:
        self.primary = primary
        self.last_used = "template"

    @property
    def name(self) -> str:
        return self.primary.name if self.primary else "template"

    def draft(self, lang: Lang, facts: dict) -> TicketDraft:
        if self.primary:
            try:
                d = self.primary.draft(lang, facts)
                self.last_used = self.primary.name
                return d
            except Exception:
                pass
        self.last_used = "template"
        return template_draft(lang, facts)


def build_drafter(cfg: DraftingConfig, api_key: str | None) -> FallbackDrafter:
    if cfg.provider == "gemini" and api_key:
        return FallbackDrafter(GeminiDrafter(cfg.model, api_key, cfg.timeout_s))
    return FallbackDrafter(None)

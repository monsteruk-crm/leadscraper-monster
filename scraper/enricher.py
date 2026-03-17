"""enricher.py — OpenAI-powered lead enrichment.

Sends a compact representation of each scraped lead to the OpenAI Chat
Completions API and asks it to:
  • fill missing fields (country, city, category, notes)
  • assign a confidence score (0.0 – 1.0)

This module is side-effect-free: it only reads and returns data.
Disable entirely via config.AI_ENRICHMENT_ENABLED = False.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from scraper.models import Lead

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a B2B lead-qualification assistant.
Given a raw scraped lead, return ONLY valid JSON with exactly these fields:
{
  "company_name": "improved or corrected name (keep original if unsure)",
  "first_name":   "contact first name if detectable from email/name/context, else empty string",
  "last_name":    "contact last name if detectable, else empty string",
  "title":        "job title / role (e.g. CEO, Sales Director, Founder) if detectable, else empty string",
  "country":      "ISO country name, or empty string",
  "city":         "city name, or empty string",
  "category":     "industry category (e.g. Sustainable Packaging, Event Services)",
  "notes":        "one-sentence summary of what this company does",
  "confidence":   0.0
}
confidence: 0.0-1.0. Use 0.0 for spam/irrelevant, 0.5 for thin data, 0.9+ for clear business with email.
If the email looks like firstname.lastname@domain.com or firstname@domain.com, infer the name from it.
Do NOT output any text outside the JSON object."""


async def enrich_lead(
    client: AsyncOpenAI,
    lead: Lead,
    model: str,
) -> Lead:
    """Enrich a Lead using OpenAI and return the updated Lead.

    Fields already populated on the input lead take precedence;
    the AI only fills blanks, improves confidence, and writes notes.

    Args:
        client: Initialised AsyncOpenAI client.
        lead:   Lead to enrich (mutated in place and returned).
        model:  OpenAI model name.

    Returns:
        The same Lead object with enriched fields.
    """
    user_prompt = (
        f"Company: {lead.company_name}\n"
        f"Website: {lead.website}\n"
        f"Email: {lead.email}\n"
        f"Phone: {lead.phone}\n"
        f"Contact: {lead.contact_name}\n"
        f"First name: {lead.first_name}\n"
        f"Last name: {lead.last_name}\n"
        f"Title: {lead.title}\n"
        f"Source URL: {lead.source_url}\n"
    )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=300,
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        data: dict = json.loads(raw)

        # Only overwrite blank fields; never discard existing data.
        lead.company_name = data.get("company_name") or lead.company_name
        lead.first_name = lead.first_name or data.get("first_name", "")
        lead.last_name = lead.last_name or data.get("last_name", "")
        lead.title = lead.title or data.get("title", "")
        lead.country = lead.country or data.get("country", "")
        lead.city = lead.city or data.get("city", "")
        lead.category = lead.category or data.get("category", "")
        lead.notes = lead.notes or data.get("notes", "")
        lead.confidence = float(data.get("confidence", lead.confidence))
        # Keep contact_name in sync
        if not lead.contact_name and (lead.first_name or lead.last_name):
            lead.contact_name = f"{lead.first_name} {lead.last_name}".strip()

        logger.debug(
            "Enriched '%s' — confidence=%.2f category='%s'",
            lead.company_name, lead.confidence, lead.category,
        )
    except json.JSONDecodeError as exc:
        logger.warning("OpenAI returned non-JSON for '%s': %s", lead.company_name, exc)
    except Exception as exc:
        logger.warning("OpenAI enrichment failed for '%s': %s", lead.company_name, exc)

    return lead

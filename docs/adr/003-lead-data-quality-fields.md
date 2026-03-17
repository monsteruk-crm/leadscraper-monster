# 003 — Lead Data Quality Fields

**Status:** Accepted

## Context

The initial migration from the local CLI produced leads that had a company name, website, and confidence score but were missing the fields that make a lead actionable for CRM import: a named contact, their job title, an email address, and a phone number.

Without at least an email or phone number, a lead has no commercial value. The Monster CRM schema expects `firstName`, `lastName`, `email`, `phone`, and `title` at minimum.

Root causes identified:
1. Email and phone regex ran on page text **after** noise removal, which stripped `<a href="mailto:">` and `<a href="tel:">` tags — the most reliable contact sources.
2. The `Lead` dataclass had no `first_name`, `last_name`, or `title` fields.
3. The AI enrichment prompt did not ask for individual name components or job titles.

## Decision

1. **Parser (`scraper/parsers.py`):** scan `mailto:` and `tel:` anchor links **before** any noise removal. Also extract name components from schema.org JSON-LD `Person` objects. Fall back to regex on cleaned text.

2. **Data model (`scraper/models.py`):** add `first_name`, `last_name`, `title` fields to the `Lead` dataclass. Keep `contact_name` as a joined alias for backwards-compatibility with CSV exports.

3. **AI enrichment (`scraper/enricher.py`):** extend the system prompt to explicitly request `first_name`, `last_name`, and `title`; populate these fields from the JSON response; sync `contact_name` automatically.

4. **Database (`db/postgres.py`):** add `first_name`, `last_name`, `title` columns to the `leads` DDL. Update the INSERT statement (now 22 positional parameters). Add `reset_db()` function and `POST /api/db/reset` endpoint so the schema can be cleanly recreated after this change.

5. **UI (`main.py`):** add First, Last, Title, Phone columns to the Leads drawer table. Add a **Reset DB** button (red) and confirmation modal to the header.

## Consequences

**Positive:**
- Leads now carry actionable contact information, making them directly importable into Monster CRM.
- Two extraction passes (HTML links first, AI second) maximise the chance of finding an email or phone on any given page.
- The `reset_db()` endpoint sets a precedent for handling breaking schema changes cleanly.

**Negative:**
- Existing leads in the database before this change lack the new columns; a DB reset is required (data loss).
- The INSERT statement now has 22 positional parameters — it must be kept in sync with the `Lead` dataclass field order manually (no ORM guard).
- AI enrichment adds latency and cost per lead; the quality improvement is contingent on the page containing visible contact information.

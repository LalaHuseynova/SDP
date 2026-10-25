"""Narrow lexical evidence checks against trusted identities and resource names."""

import re


def evidence_excerpt(request):
    """Keep a bounded verbatim fragment; it is provenance, not an intent proof."""
    return request if len(request) <= 4000 else request.rstrip()[-4000:]


def display_reference(reference):
    """Bound quoted identity text so a valid alias fits a clarification question."""
    return reference if len(reference) <= 120 else reference[:120] + "…"


def mentions(request, label):
    return list(re.finditer(r"(?<!\w)" + re.escape(label) + r"(?!\w)", request, flags=re.IGNORECASE))


def resource_mentioned(request, resource):
    if mentions(request, resource.resource_id) or mentions(request, resource.name):
        return True
    # Permit reordered words, e.g. "the update for Project X". This is a lexical
    # scope guard, not general semantic entailment or synonym resolution.
    tokens = set(re.findall(r"\w+", resource.name.casefold()))
    return bool(tokens) and tokens <= set(re.findall(r"\w+", request.casefold()))


def recipient_mentions(request, context):
    spans = {}
    for person in context.trusted_resource_metadata.recipients:
        for label in [person.name, person.recipient_id, *person.aliases]:
            for match in mentions(request, label):
                spans.setdefault((match.start(), match.end()), set()).add(person.recipient_id)
    longest = [span for span in spans if not any(
        other != span and other[0] <= span[0] and other[1] >= span[1] for other in spans)]
    recipients, ambiguous = [], []
    for start, end in sorted(longest):
        ids = sorted(spans[(start, end)])
        if len(ids) > 1:
            ambiguous.append(dict(field="recipient", question=f"Which recipient is '{display_reference(request[start:end])}'?",
                                  candidate_ids=ids))
        elif not any(r["recipient_id"] == ids[0] for r in recipients):
            recipients.append(dict(recipient_id=ids[0], request_reference=request[start:end]))
    return recipients, ambiguous

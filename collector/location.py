"""Metadata-only extraction. Never infer residence from cities or event locations."""
import re
from datetime import datetime, timezone
STATES = ('Andhra Pradesh','Arunachal Pradesh','Assam','Bihar','Chhattisgarh','Goa','Gujarat','Haryana','Himachal Pradesh','Jharkhand','Karnataka','Kerala','Madhya Pradesh','Maharashtra','Manipur','Meghalaya','Mizoram','Nagaland','Odisha','Punjab','Rajasthan','Sikkim','Tamil Nadu','Telangana','Tripura','Uttar Pradesh','Uttarakhand','West Bengal')
def extract_public_location(bio, source_url):
    result = dict(location_text='', location_state='', location_source=source_url,
                  location_confidence='unknown', location_last_verified=datetime.now(timezone.utc).date().isoformat())
    # Explicit present residence only; origin, tours, and city abbreviations are not residence.
    for line in str(bio or '').splitlines():
        match = re.search(r'\b(?:based in|living in|residing in|resident of)\s+(.+)', line, re.I)
        if not match:
            continue
        evidence = match.group(1)
        states = [state for state in STATES if re.search(r'\b'+re.escape(state)+r'\b', evidence, re.I)]
        result['location_text'] = line.strip()
        if len(states) == 1 and not re.search(r'\b(?:or|between|moving|soon|formerly)\b|/', evidence, re.I):
            result['location_state'] = states[0]
            result['location_confidence'] = 'self-disclosed present residence; not independently verified'
        else:
            result['location_confidence'] = 'ambiguous; manual review required'
        return result
    return result

def location_candidate(username, bio, source_url):
    """Return a reviewable candidate; never promote it into verified creator state."""
    return {'username': str(username).lstrip('@'), 'bio_evidence': str(bio),
            'review_status': 'pending', **extract_public_location(bio, source_url)}

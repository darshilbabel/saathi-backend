import pycountry


def get_iso_language_choices():
    """Build (iso_code, display_name) choices from pycountry for the admin's Language.iso_code dropdown.

    Prefers alpha_2 (matches existing legacy Voice.language values like 'en', 'hi', 'ta');
    falls back to alpha_3 for languages pycountry only tracks a 3-letter code for.
    """
    choices = []
    for language in pycountry.languages:
        code = getattr(language, "alpha_2", None) or getattr(language, "alpha_3", None)
        if not code:
            continue
        choices.append((code, f"{language.name} ({code})"))
    return sorted(choices, key=lambda choice: choice[1])


def resolve_iso_language(code):
    """Resolve a language code to its pycountry entry, trying alpha_2 then alpha_3. Returns None if unresolvable."""
    if not code:
        return None
    return pycountry.languages.get(alpha_2=code) or pycountry.languages.get(alpha_3=code)

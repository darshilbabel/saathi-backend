import logging

import pycountry
from django.db import migrations

logger = logging.getLogger('django')


def seed_flow_languages(apps, schema_editor):
    """Ensure every language code actually in use across existing Flow.languages values has a
    matching Language row, before the admin's language checkboxes start being sourced from
    Language — otherwise any in-use code without a row silently disappears from that Flow's
    list the next time it's saved."""
    Flow = apps.get_model('chatbot', 'Flow')
    Language = apps.get_model('chatbot', 'Language')

    codes = set()
    for languages in Flow.objects.values_list('languages', flat=True):
        if languages:
            codes.update(languages)

    existing = set(Language.objects.filter(iso_code__in=codes).values_list('iso_code', flat=True))
    for code in codes - existing:
        pycountry_language = pycountry.languages.get(alpha_2=code) or pycountry.languages.get(alpha_3=code)
        if pycountry_language:
            Language.objects.create(iso_code=code, name=pycountry_language.name)
        else:
            logger.warning(
                "Flow language seed: %r is not a known ISO 639 code — creating a best-effort Language row.", code
            )
            Language.objects.create(iso_code=code, name=code)


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0095_language_provider_config'),
    ]

    operations = [
        migrations.RunPython(seed_flow_languages, migrations.RunPython.noop),
    ]

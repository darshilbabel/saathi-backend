import logging

import pycountry
from django.db import migrations

logger = logging.getLogger('django')


def seed_chatsession_bot_vernacular_languages(apps, schema_editor):
    """Ensure every language code actually in use across existing ChatSession.language
    and BotVernacular.language values has a matching Language row, before their admin
    forms start being sourced from Language — otherwise any in-use code without a row
    silently becomes unselectable in the new dropdown the next time that row is saved.
    Same pattern as 0096_seed_flow_languages.py, applied here to two more fields."""
    ChatSession = apps.get_model('chatbot', 'ChatSession')
    BotVernacular = apps.get_model('chatbot', 'BotVernacular')
    Language = apps.get_model('chatbot', 'Language')

    codes = set()
    codes.update(ChatSession.objects.exclude(language__isnull=True).exclude(language='')
                 .values_list('language', flat=True).distinct())
    codes.update(BotVernacular.objects.exclude(language__isnull=True).exclude(language='')
                 .values_list('language', flat=True).distinct())

    existing = set(Language.objects.filter(iso_code__in=codes).values_list('iso_code', flat=True))
    for code in codes - existing:
        pycountry_language = pycountry.languages.get(alpha_2=code) or pycountry.languages.get(alpha_3=code)
        if pycountry_language:
            Language.objects.create(iso_code=code, name=pycountry_language.name)
        else:
            logger.warning(
                "ChatSession/BotVernacular language seed: %r is not a known ISO 639 code — "
                "creating a best-effort Language row.", code
            )
            Language.objects.create(iso_code=code, name=code)


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0101_chatsession_language_ref'),
    ]

    operations = [
        migrations.RunPython(seed_chatsession_bot_vernacular_languages, migrations.RunPython.noop),
    ]

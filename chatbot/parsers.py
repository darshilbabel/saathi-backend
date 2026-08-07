import codecs

from django.conf import settings
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser
from rest_framework.utils import json


def _reject_duplicate_keys(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise ParseError(f'Duplicate key "{key}" in request body.')
        seen.add(key)
    return dict(pairs)


class StrictJSONParser(JSONParser):
    """Like DRF's JSONParser, but rejects a JSON object with duplicate keys instead of
    silently keeping only the last occurrence."""

    def parse(self, stream, media_type=None, parser_context=None):
        parser_context = parser_context or {}
        encoding = parser_context.get('encoding', settings.DEFAULT_CHARSET)
        try:
            decoded_stream = codecs.getreader(encoding)(stream)
            parse_constant = json.strict_constant if self.strict else None
            return json.load(
                decoded_stream,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=parse_constant,
            )
        except ParseError:
            raise
        except ValueError as exc:
            raise ParseError('JSON parse error - %s' % str(exc))
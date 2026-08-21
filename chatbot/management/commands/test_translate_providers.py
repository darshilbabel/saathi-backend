"""
Quick eval script to compare translation providers (Google, Sarvam, AI4Bharat)
side by side on the same input text.
Calls the same `text_translate_provider` function used by the
`/api/text_translate/` endpoint (chatbot/views/bhashini_views.py), but passes
an in-memory (unsaved) Voice object per provider instead of resolving one from
a route's DB config - this avoids needing a Sarvam-configured route to exist.
Usage:
    python manage.py test_translate_providers --input inputs.json --source en --target hi
    python manage.py test_translate_providers --input inputs.json --target hi --providers google,sarvam
Input file is a JSON array of strings, e.g.:
    ["How are you feeling today?", "Please attend the meeting on Friday."]
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from chatbot.constants.voice_provider_defaults import get_provider_defaults
from chatbot.models import GenderChoices, Voice, VoiceProvider, VoiceType
from chatbot.utils.audio_provider_utils import text_translate_provider

PROVIDER_MAP = {
    "google": VoiceProvider.GOOGLE,
    "sarvam": VoiceProvider.SARVAM,
    "ai4bharat": VoiceProvider.AI4Bharat,
}


class Command(BaseCommand):
    help = "Compare translation providers (google/sarvam/ai4bharat) on a list of input texts."

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True, help='Path to a JSON file containing an array of strings, e.g. ["text one", "text two"]')
        parser.add_argument("--source", default="en", help="Source language code (default: en)")
        parser.add_argument("--target", required=True, help="Target language code, e.g. hi/kn/ta/or")
        parser.add_argument("--output", default="translated_output.json", help="Output JSON file path")
        parser.add_argument(
            "--providers", default="google,sarvam,ai4bharat",
            help="Comma-separated subset of: google,sarvam,ai4bharat (default: all)"
        )

    def handle(self, *args, **options):
        input_path = options["input"]
        source_language = options["source"]
        target_language = options["target"]
        output_path = options["output"]
        providers = [p.strip().lower() for p in options["providers"].split(",") if p.strip()]

        if Path(input_path).resolve() == Path(output_path).resolve():
            raise CommandError("--input and --output must not point to the same file.")

        with open(input_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            self.stderr.write(self.style.ERROR(f"{input_path} must be a JSON array of strings"))
            return

        inputs = [text.strip() for text in raw if text.strip()]

        if not inputs:
            self.stderr.write(self.style.ERROR(f"No non-empty strings found in {input_path}"))
            return

        results = {
            "meta": {
                "source_language": source_language,
                "target_language": target_language,
                "input_count": len(inputs),
            }
        }

        for provider_key in providers:
            provider_enum = PROVIDER_MAP.get(provider_key)
            if not provider_enum:
                self.stderr.write(self.style.WARNING(f"Skipping unknown provider: {provider_key}"))
                continue

            voice_provider = Voice(
                provider=provider_enum,
                type=VoiceType.TextToText,
                gender=GenderChoices.MALE,
                other_params=get_provider_defaults(provider_enum, VoiceType.TextToText),
            )

            self.stdout.write(f"Running provider: {provider_key} ({len(inputs)} inputs)")
            provider_results = []
            for text in inputs:
                try:
                    response = text_translate_provider(
                        message_body=text,
                        source_language=source_language,
                        target_language=target_language,
                        voice_provider=voice_provider,
                    )
                    status = response.get("status")
                    content = response.get("content")
                except Exception as e:
                    status, content = 500, str(e)

                provider_results.append({
                    "input": text,
                    "output": content if status == 200 else None,
                    "status": status,
                    "error": None if status == 200 else content,
                })

            results[provider_key] = provider_results

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        self.stdout.write(self.style.SUCCESS(f"Wrote results to {output_path}"))

        # Quick console table per provider for a fast eyeball comparison
        for provider_key in providers:
            if provider_key not in results:
                continue
            self.stdout.write(f"\n=== {provider_key} ===")
            for row in results[provider_key]:
                out = row["output"] if row["output"] is not None else f"ERROR: {row['error']}"
                self.stdout.write(f"  IN : {row['input']}")
                self.stdout.write(f"  OUT: {out}\n")

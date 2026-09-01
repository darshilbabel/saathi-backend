# Language / Provider Config

## Context

`Voice.language` and `Voice.provider` (`chatbot/models/company_models.py`) were a free-text `CharField` and an enum `CharField`. `language` had no validation at all — any string could be typed into the admin inline — and there was no structured way to say "for Odia on Sarvam specifically, send the code `od` instead of `or`" other than hardcoding it into `LanguageMapping` (`chatbot/models/enums.py:397-428`), which already has exactly one such hardcoded exception.

The HLD (`Shikshalokam_HLD-Language Provider Config.drawio.png`) introduced three new tables — `Language` (ISO-backed), `Provider` (admin-managed), `LanguageProviderConfig` (per-`(language, provider)` code override) — plus FKs from `Voice`, while keeping the old `language`/`provider` columns as a frozen, auto-synced compatibility shim so the ~20+ existing call sites that filter `Voice.objects.filter(..., language=<str>)` across the codebase keep working untouched.

**Note:** this doc describes the implementation as built, which diverges from the original HLD in two ways (both per follow-up review):
- `Voice` does **not** have a `language_provider_config` FK. A `LanguageProviderConfig` override is looked up live by `(voice.language_ref, voice.provider_ref)` at call time instead of being separately selected per Voice row — since the override is fully determined by that pair, storing a redundant selectable FK added a field to the admin form for no reason. `LanguageProviderConfig`'s uniqueness is therefore `(language, provider)`, not `(language, provider, custom_code)`.
- `LanguageProviderConfig` has no standalone admin page — only a `TabularInline` under `ProviderAdmin` (a provider's language overrides are naturally viewed provider-first).

Decisions locked in with the user:
- Build now (implementation-ready).
- `Language.iso_code`'s dropdown is sourced from the **`pycountry`** package (new dependency).
- `Provider` rows are **openly admin-nameable**, decoupled from dispatch capability via a slug-keyed registry with a clear error when a `Provider` has no registered handler.
- The `custom_code` override is resolved **once, early** — in `get_voice_provider()` — not re-resolved inside each provider-call function.
- One squashed migration file for the whole feature (single PR), not a multi-file staged rollout.

Branch: `feature/language-provider-config` (off `drop_10_work`). Migration: `chatbot/migrations/0095_language_provider_config.py` (depends on `0094_alter_companybot_tool_context_and_more`).

---

## 1. New models — `chatbot/models/language_provider_models.py`

- **`Language`**: `iso_code` (unique `CharField`, no model-level choices — see §2), `name`, timestamps. `clean()` re-validates `iso_code` against `pycountry` as a backstop for non-admin creation paths.
- **`Provider`**: `name` (unique), `slug` (`SlugField`, unique, auto-filled from `name` in `save()` if blank), timestamps, `HistoricalRecords`.
- **`LanguageProviderConfig`**: FK `language`, FK `provider`, `custom_code` (blank-default `""`), timestamps, `HistoricalRecords`. `UniqueConstraint(fields=['language', 'provider'], name='unique_language_provider')` — at most one override per pair, since nothing selects a specific config row anymore.

## 2. `pycountry` integration

Added `pycountry` to `pyproject.toml` via `uv add pycountry`.

`Language.iso_code` stays a plain `CharField` at the model level (`pycountry.languages` has ~7000 entries — too many for `TextChoices`). The dropdown lives at the **admin form layer**: `LanguageAdminForm` with `iso_code = forms.ChoiceField(choices=get_iso_language_choices)`, built via `chatbot/utils/pycountry_utils.py`.

Existing legacy `Voice.language` values (`en`, `hi`, `kn`, `te`, `or`, `ta`) all resolve via `pycountry.languages.get(alpha_2=code)`. The backfill never crashes on an unresolvable value — falls back to `Language.objects.get_or_create(iso_code=code, defaults={'name': code})` with a warning log.

## 3. Provider slug → dispatch registry — `chatbot/constants/provider_dispatch.py`

`chatbot/constants/provider_slugs.py` holds the slug constants and the `VOICE_PROVIDER_TO_SLUG`/`SLUG_TO_VOICE_PROVIDER` maps — kept dependency-light (only imports `chatbot.models.enums`) so `company_models.py` can import it without a circular-import risk through `chatbot.translate.*` (which itself imports `chatbot.models`).

`chatbot/constants/provider_dispatch.py` holds four registries — `TTS_DISPATCH`, `STT_DISPATCH`, `TRANSLATE_DISPATCH`, `TRANSLITERATE_DISPATCH` — each `dict[provider_slug -> adapter_fn]`, seeded for the 5 currently-working providers (`google`, `ai4bharat`, `openai-whisper`, `sarvam`, `custom-llm`). Each adapter wraps an existing `chatbot/translate/*/*.py` function unchanged. `get_handler(registry, provider_slug, operation_label)` raises `NoDispatchHandlerError` (lists registered slugs) instead of a silent `{'status': 500, 'content': "No provider found!"}`.

Replaced the `if voice_provider.provider == VoiceProvider.X: ...` chains in `chatbot/utils/audio_provider_utils.py` (`_call_single_tts`, `speech_text_provider`, `_dispatch_translation`) **and** `chatbot/utils/transliterate_utils.py` (`transliterate_text` — found via `get_voice_provider` import, not caught by the original signature survey) with `get_handler(...)` lookups keyed by a new `Voice.provider_slug` property (`provider_ref.slug` when set, else `VOICE_PROVIDER_TO_SLUG.get(self.provider)` for unsaved/legacy-only instances — this fallback path is exercised by the existing `test_translate_providers` management command, which passes an in-memory `Voice(provider=...)` with no `provider_ref`).

## 4. `Voice` model changes — `chatbot/models/company_models.py`

```python
language_ref = models.ForeignKey('chatbot.Language', on_delete=models.PROTECT, related_name='voices')
provider_ref = models.ForeignKey('chatbot.Provider', on_delete=models.PROTECT, related_name='voices')
```
Named `language_ref`/`provider_ref`, not `language_id`/`provider_id` as in the HLD — those names collide with Django's auto-generated `<fieldname>_id` shorthand for the existing `language`/`provider` CharFields, which are staying. `on_delete=PROTECT` — a `Language`/`Provider` row must not vanish out from under a live Voice config.

`_sync_legacy_fields_from_refs()` (called from both `clean()` and `save()` — see note below) sets `self.language = self.language_ref.iso_code` and `self.provider = SLUG_TO_VOICE_PROVIDER.get(self.provider_ref.slug, self.provider)` when the FKs are set.

**Correctness fix vs. original plan**: the sync can't live only in `save()`. `clean()`'s uniqueness check (`Voice.objects.filter(company_bot=..., type=..., language=self.language)`) runs *before* `save()` via `full_clean()` (e.g. from admin forms), so a row created via the FK fields alone would see a stale/blank legacy `language` at validation time if sync only happened in `save()`. Both call the same `_sync_legacy_fields_from_refs()` helper.

`clean()`'s duplicate/fallback-match checks and the `unique_primary_voice_per_bot_type_language` constraint stay on the legacy `language` field, unchanged — correct as long as sync is reliable; migrating them to `language_ref` is a deferred follow-up, not part of this change.

## 5. Migration — `chatbot/migrations/0095_language_provider_config.py`

One file, sequenced internally (safe within a single transaction since it's one PR, not a staged multi-deploy rollout):
1. `CreateModel` for `Language`, `Provider`, `LanguageProviderConfig` (+ `Historical*` shadow tables).
2. `AddField` `language_ref`/`provider_ref` to `Voice`/`HistoricalVoice`, nullable at this point.
3. `RunPython` seed — 5 `Provider` rows matching `provider_dispatch.py`'s slugs.
4. `RunPython` backfill — resolves every existing `Voice` row's legacy `(language, provider)` pair to `Language`/`Provider` FKs, via `Voice._default_manager` (the historical model has no `.objects` since `Voice`'s custom managers aren't `use_in_migrations`-flagged), bulk `.update()` per distinct pair.
5. `AlterField` tightening `language_ref`/`provider_ref` to `NOT NULL` on `Voice` (verified locally: 0 unresolved rows after step 4, across 76 existing rows / 11 distinct `(language, provider)` pairs).

`HistoricalVoice.language_ref`/`provider_ref` stay nullable — `django-simple-history` always forces historical FK fields to `null=True` regardless of the source field's nullability, matching every other FK on `Historical*` models in this app.

## 6. `get_voice_provider()` — `chatbot/utils/audio_provider_utils.py`

Returns `(voice, effective_language)`. `effective_language` is looked up live: `LanguageProviderConfig.objects.filter(language=voice.language_ref, provider=voice.provider_ref).exclude(custom_code="").first()` — its `custom_code` if found, else the same `language` value used to find the voice (a no-op unless a config row exists). The three callers (`text_speech_provider`, `speech_text_provider`, `text_translate_provider`/`_dispatch_translation`, and `transliterate_text`) substitute `effective_language` in place of the raw string. For translation/transliteration (two legs), only the leg that actually matched the voice is substituted — the other (typically `en`) passes through unchanged. The English-fallback branch (no voice for the requested language, falls back to an `en`-configured voice) never applies an override — that fallback voice matched on `en`, not the language actually being spoken.

`LanguageMapping` (`enums.py:397-428`) is untouched — `effective_language` still flows through `get_mapped_language()`/`get_sarvam_language()`/etc. exactly as before. A `LanguageProviderConfig(Odia, Sarvam, custom_code="od")` row produces the same `"od-IN"` Sarvam already special-cases today (verified in shell).

## 7. Admin — `chatbot/admin/company_admin.py` + `chatbot/admin/language_provider_admin.py`

`LanguageAdmin` (pycountry-backed dropdown), `ProviderAdmin` (`prepopulated_fields`, nested `LanguageProviderConfigInline`). No standalone `LanguageProviderConfig` admin page.

`VoiceProviderAdmin`/`FallbackVoiceProviderAdmin` `fields` swap `'language', 'provider'` for `'language_ref', 'provider_ref'` (no `language_provider_config` field — nothing to select). `order_by('type', 'language')` → `order_by('type', 'language_ref__name')`.

## 8. `bot_resource.py` (django-import-export)

**Pre-existing bug found and fixed**: `CompanyBotResource` overrode `after_import_instance(self, instance, new, **kwargs)`, which `django-import-export` 4.4.0 no longer calls at all — that name is a deprecated shim; the real hook is now `after_init_instance(instance, new, row, ...)`, which fires *before* the instance is saved (too early — `Voice.company_bot` needs a saved `CompanyBot`). The whole voices/state-machines import feature was silently a no-op before this change, unrelated to this task. Fixed by moving the logic to `after_import_row(self, row, row_result, **kwargs)`, which fires post-save (`instance = CompanyBot.objects.filter(pk=row_result.object_id).first()`).

`_create_voice()` now resolves `Language`/`Provider` instances from the imported legacy strings before `Voice.objects.create(...)`; skips the row with a `logger.warning` if either doesn't resolve (cross-environment import referencing an unseeded language/provider), rather than raising.

`duplicate_bot()`: no change needed — clones via full-instance `pk=None; voice.save()`, which copies the new FKs automatically (verified).

## 9. `Flow.languages` now sources from `Language` — `chatbot/admin/company_admin.py`, `chatbot/migrations/0096_seed_flow_languages.py`

`Flow.languages` (`chatbot/models/company_models.py:828`) stays a plain `JSONField` of iso-code strings — **no schema change**. Only the admin form changed: `FlowAdminForm`'s `languages` checkbox choices (`MultipleChoiceField` + `CheckboxSelectMultiple`) now come from `Language.objects.values_list('iso_code', 'name')`, built fresh in `__init__()` per request, instead of a hardcoded `LANGUAGE_CHOICES` constant (removed). `clean_languages()`'s allowed-set switched the same way.

**Data-loss risk identified and closed before switching the form**: a code only renders as a checkbox if a matching `Language` row exists — any in-use code without one would silently vanish from that Flow's list the next time it's saved. `chatbot/migrations/0096_seed_flow_languages.py` closes this: it unions every distinct code actually found across **all existing `Flow.languages` values** (not a hardcoded/assumed list) and seeds a `Language` row per code via `pycountry`, before the form switch takes effect — same pattern, same graceful unresolvable-code fallback, as the `Voice` backfill in §5. Verified locally (all in-use codes were already covered; 0 rows would've been affected).

Deliberately scoped to `Flow` only — did **not** pre-seed `StoryLanguageChoices`' codes (`en/hi/kn/te/or/ta`, for `ChatSession`, see below), to keep this independent of the deferred `ChatSession` work.

**Pre-existing bug found and fixed along the way**: `FlowAdminForm.clean_languages()` raised `pydantic.ValidationError` (wrong import — `from pydantic import ValidationError` at the top of `company_admin.py`) instead of Django's. `pydantic.ValidationError` doesn't accept a plain string message, so submitting duplicate language codes crashed with an unhandled `TypeError` (500) instead of a clean field error. Fixed by importing `ValidationError` from `django.core.exceptions`; confirmed the crash before the fix and the clean error after, in shell.

Known gap, not fixed (by design, not a bug): `te` (Telugu) is in `Flow.languages`' Python-level default and the admin form's "new flow" initial suggestion, but isn't seeded since no existing `Flow` row uses it (the migration only seeds what's actually in use) — so a new Flow's suggested default won't show a Telugu checkbox until either a real Flow uses it or someone adds that `Language` row once via its own admin page. No data-loss risk (nothing existing references it).

## Deferred — not implemented

**`ChatSession.language` → `Language`**: `ChatSession.language` (`chatbot/models/chat_models.py:13`) is the same shape problem `Voice.language` was (plain `CharField(choices=StoryLanguageChoices.choices)`), but written directly at ~14 `ChatSession.objects.get_or_create/create(...)` call sites across websocket consumers (`async_consumer.py`, `free_flow_consumer.py`, `one_shot_bedrock_consumer.py`, etc.) and views — a much wider, hotter write surface than `Voice` ever had, several of which are legacy Mitra-derived code slated for a future cleanup (Saathi's live path runs through the common-ws consumer group).

Recommended approach when this is picked back up (intentionally the reverse sync direction from `Voice`, so it needs **zero changes** to any of those ~14 call sites regardless of which are current vs. soon removed):
- Add `ChatSession.language_ref` — nullable FK to `Language`, `on_delete=SET_NULL` (purely a derived convenience field, not load-bearing).
- Auto-derive it in a new `save()` override: `language_ref = Language.objects.filter(iso_code=self.language).first()`.
- One-off data migration backfills `language_ref` for existing rows the same way.
- `Language` rows for `StoryLanguageChoices`' codes (`en/hi/kn/te/or/ta`) already verified against `pycountry` (§2/§9) — same deterministic resolution applies whenever this is built.

**`BotVernacular.language` → `Language`**: `BotVernacular.language` (`chatbot/models/bot_vernacular_model.py:14`) is an even less structured version of the same problem — plain free-text `CharField`, no `choices` at all today. Write surface is much smaller than `ChatSession`'s: a single call site, `chatbot/views/admin/bot_admin_views.py`, plus direct edits via its own registered admin (`BotVernacularAdmin`, `chatbot/admin/bot_vernacular_admin.py` — `language` is a plain text input there, no dropdown yet).

Same recommended shape as `ChatSession` — nullable `language_ref` FK, `on_delete=SET_NULL`, auto-derived in `save()` from the existing `language` string, one-off backfill migration, zero changes to the one write call site. Once built, `BotVernacularAdmin.list_filter`/`search_fields` could optionally switch `language` to a `Language`-backed dropdown the same way `Flow` did (§9) — not required, since unlike `Flow` this field has no fixed choice list to begin with (nothing to lose from an unseeded code) and no checkbox rendering it depends on today.

Worth a passing note, not scoped here: `StoryVernacular` (`chatbot/models/story_vernacular_model.py`, same admin file) has the identical plain-`CharField` `language` shape — same treatment would apply if/when it's picked up.

**Both explicitly on hold until the Mitra cleanup task is done** — picked back up after, per the user.

## Verification performed

1. `makemigrations --check --dry-run` — clean.
2. Full local `migrate` — 76 existing `Voice` rows backfilled, 0 left with a null FK.
3. Shell spot-check: `v.language_ref.iso_code == v.language`, `v.provider_ref.slug` maps correctly both ways.
4. Created `Language(Odia)` + reused seeded `Provider(Sarvam)` + `LanguageProviderConfig(custom_code="od")`, created a `Voice` via the FKs, confirmed legacy fields auto-synced and `get_voice_provider()` resolved `effective_language == "od"`.
5. `Provider` with an unregistered slug → confirmed `NoDispatchHandlerError` with a clear message.
6. `provider_slug` fallback verified for unsaved in-memory `Voice` objects (the `test_translate_providers` management command's usage pattern).
7. `duplicate_bot` clone logic simulated — new FKs carried over correctly.
8. `CompanyBotResource` export/import round-trip via `resource.import_data()` — voices now actually import with correct FKs (see the `after_import_row` fix above).
9. Admin pages rendered via test client: `CompanyBot` change form, `Language`/`Provider` add forms, `LanguageProviderConfig` standalone URL confirmed 404 (inline-only, as intended).
10. `FlowAdminForm`: confirmed every code in every existing `Flow.languages` row already had a matching `Language` row post-migration (no gap in this DB); confirmed `languages` choices reflect the live `Language` table; confirmed a duplicate-code submission now returns a clean field error instead of crashing (`TypeError` before the fix, reproduced then fixed); confirmed an unrecognized code (`'zz'`) is rejected cleanly; confirmed no stray `Flow` rows were persisted by the test forms.

## Critical files

- `chatbot/models/company_models.py` (`Voice` changes)
- `chatbot/models/language_provider_models.py` (new)
- `chatbot/models/__init__.py`, `chatbot/admin/__init__.py` (import wiring)
- `chatbot/utils/audio_provider_utils.py`, `chatbot/utils/transliterate_utils.py` (dispatch + `get_voice_provider`)
- `chatbot/constants/provider_dispatch.py`, `chatbot/constants/provider_slugs.py` (new)
- `chatbot/utils/pycountry_utils.py` (new)
- `chatbot/admin/company_admin.py`, `chatbot/admin/language_provider_admin.py` (new)
- `chatbot/resources/bot_resource.py`
- `chatbot/migrations/0095_language_provider_config.py` (new, single file)
- `chatbot/migrations/0096_seed_flow_languages.py` (new — `Flow.languages` backfill)
- `pyproject.toml` / `uv.lock` (`pycountry`)

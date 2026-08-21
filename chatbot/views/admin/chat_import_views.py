"""
Dev tool: ingest a company-chat export (.xlsx, a raw dump of CompanyChat rows)
from another instance into this instance's admin panel.
Disabled unless the environment variable COMPANY_CHAT_IMPORT_TOOL_ENABLED=True is set (see chat_import_tool_enabled()).
"""
import ast
import logging
import os
import time

import pandas as pd
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views import View

from chatbot.models import (
    ChatSession, Company, CompanyBot, CompanyChat, CompanyChatSourceChoices, Profile, ProfileType,
)

logger = logging.getLogger('django')

AI_SENDER_NAME = 'ai'
EMPTY_VALUES = ('', 'none', 'nan')
REQUIRED_COLUMNS = ('session', 'message')


def chat_import_tool_enabled():
    """Dev tool: ingest a company-chat export (.xlsx) from another instance via
    the admin panel. Disabled unless COMPANY_CHAT_IMPORT_TOOL_ENABLED=True is set
    in the environment (.env) - not a Django setting, checked directly here."""
    return os.getenv('COMPANY_CHAT_IMPORT_TOOL_ENABLED', 'False') == 'True'


def _clean_cell(value):
    """The export renders Python None as the literal string 'None' (or blank) -
    normalize that (and stray whitespace) to a real None."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in EMPTY_VALUES else text


def _parse_other_params(value):
    value = _clean_cell(value)
    if value is None:
        return None
    try:
        parsed = ast.literal_eval(value)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, SyntaxError):
        return None


def _parse_created_at(value):
    value = _clean_cell(value)
    if value is None:
        return timezone.now()
    parsed = parse_datetime(value)
    return parsed or timezone.now()


def _generate_session_id():
    """Same mechanism as generate_session_id() in chatbot/views/api_views.py, so
    imported sessions get ids in the same format/shape as normal frontend-issued ones."""
    session = SessionStore()
    session.create()
    return session.session_key


def _resolve_unique_session_id(file_session_id):
    """Never reuse a session id already owned by an existing (possibly unrelated)
    ChatSession - mint a fresh one instead. Returns (target_session_id, collided)."""
    if not ChatSession.objects.filter(session=file_session_id).exists():
        return file_session_id, False
    while True:
        candidate = _generate_session_id()
        if not ChatSession.objects.filter(session=candidate).exists():
            return candidate, True


@method_decorator(staff_member_required, name='dispatch')
class CompanyChatImportView(View):
    template_name = 'admin/company_chat_import.html'

    def dispatch(self, request, *args, **kwargs):
        if not chat_import_tool_enabled():
            raise Http404()

        self.profile = Profile.objects.filter(email=request.user.email).select_related('company').first()
        self.is_moderator = bool(self.profile and self.profile.profile_type == ProfileType.MODERATOR)
        if not (request.user.is_superuser or self.is_moderator):
            raise PermissionDenied("You don't have permission to use this tool.")

        return super().dispatch(request, *args, **kwargs)

    def _companies_queryset(self, request):
        if request.user.is_superuser:
            return Company.objects.all().order_by('name')
        return Company.objects.filter(id=self.profile.company_id)

    def _build_context(self, request):
        companies = self._companies_queryset(request)
        return {
            'opts': ChatSession._meta,
            'companies': companies,
            'bots': CompanyBot.objects.filter(company__in=companies).select_related('company').order_by(
                'company__name', 'name'
            ),
            # Unfiltered by design: Profile is valid either as userid-only (UMS, no
            # company) or company+email, so the picker lists every profile as-is.
            'profiles': Profile.objects.select_related('company').order_by('company__name', 'first_name'),
        }

    def get(self, request):
        return render(request, self.template_name, self._build_context(request))

    def post(self, request):
        context = self._build_context(request)

        company = context['companies'].filter(id=request.POST.get('company')).first()
        if not company:
            messages.error(request, "Please select a valid company.")
            return render(request, self.template_name, context)

        company_bot = CompanyBot.objects.filter(id=request.POST.get('company_bot'), company=company).first()
        if not company_bot:
            messages.error(request, "Please select a bot that belongs to the selected company.")
            return render(request, self.template_name, context)

        override_profile = None
        profile_id = request.POST.get('profile')
        if profile_id:
            override_profile = Profile.objects.filter(id=profile_id).first()
            if not override_profile:
                messages.error(request, "Please select a valid profile.")
                return render(request, self.template_name, context)

        uploaded_file = request.FILES.get('import_file')
        if not uploaded_file:
            messages.error(request, "Please choose an .xlsx file to import.")
            return render(request, self.template_name, context)

        try:
            df = pd.read_excel(uploaded_file, dtype=str, keep_default_na=False)
        except Exception as e:
            logger.error("Failed to read uploaded chat export: %s", e, exc_info=True)
            messages.error(request, f"Could not read the uploaded file: {e}")
            return render(request, self.template_name, context)

        missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
        if missing:
            messages.error(request, f"The file is missing required column(s): {', '.join(missing)}.")
            return render(request, self.template_name, context)

        context['import_done'] = True
        context['session_results'] = self._ingest(df, company, company_bot, override_profile)
        return render(request, self.template_name, context)

    def _ingest(self, df, company, company_bot, override_profile):
        results = []
        for file_session_id, group in df.groupby('session', sort=False):
            if _clean_cell(file_session_id) is None:
                results.append({
                    'file_session_id': file_session_id or '(blank)',
                    'status': 'skipped',
                    'detail': f"{len(group)} row(s) with no session id were skipped.",
                    'message_count': 0,
                })
                continue
            try:
                with transaction.atomic():
                    results.append(self._ingest_session_group(
                        file_session_id, group, company, company_bot, override_profile
                    ))
            except Exception as e:
                logger.error("Failed importing session '%s': %s", file_session_id, e, exc_info=True)
                results.append({
                    'file_session_id': file_session_id,
                    'status': 'error',
                    'detail': str(e),
                    'message_count': 0,
                })
        return results

    def _ingest_session_group(self, file_session_id, group, company, company_bot, override_profile):
        target_session_id, collided = _resolve_unique_session_id(file_session_id)

        rows = group.sort_values('created_at') if 'created_at' in group.columns else group

        name_to_profile = {}
        if override_profile is None:
            # No profile was picked on the form - fall back to auto-creating one
            # temp Profile per distinct human sender/receiver name in this session.
            human_names = set()
            for column in ('sender', 'receiver'):
                if column in rows.columns:
                    for raw_name in rows[column]:
                        name = _clean_cell(raw_name)
                        if name and name.lower() != AI_SENDER_NAME:
                            human_names.add(name)

            epoch = int(time.time())
            name_to_profile = {
                name: Profile.objects.create(
                    first_name=name,
                    company=company,
                    userid=f"{target_session_id}-{epoch}-{name}",
                    profile_type=ProfileType.USER,
                )
                for name in human_names
            }

        ai_profile = Profile.objects.get(id=1)

        def resolve_profile(raw_name):
            name = _clean_cell(raw_name)
            if name is None:
                # Blank cell - if a single profile was picked on the form, attribute the
                # unnamed side of the message to them; otherwise we can't guess which
                # (possibly several) auto-created profile it belongs to.
                return override_profile
            if name.lower() == AI_SENDER_NAME:
                return ai_profile
            return override_profile if override_profile is not None else name_to_profile.get(name)

        session_status = _clean_cell(rows.iloc[-1].get('status')) if 'status' in rows.columns and len(rows) else None
        session_type = (
            _clean_cell(rows.iloc[-1].get('session_type')) if 'session_type' in rows.columns and len(rows) else None
        )

        session_profile = override_profile or next(iter(name_to_profile.values()), None)

        chat_session = ChatSession.objects.create(
            session=target_session_id,
            profile=session_profile,
            company_bot=company_bot,
            session_type=session_type or 'imported',
            session_status=session_status,
            user_id=session_profile.userid if session_profile else None,
        )

        chat_objects = []
        for _, row in rows.iterrows():
            other_params = _parse_other_params(row.get('other_params'))
            audio_ref = _clean_cell(row.get('audio_file'))
            if audio_ref:
                other_params = other_params or {}
                other_params['imported_audio_file_ref'] = audio_ref

            chat_objects.append(CompanyChat(
                message=row.get('message') or '',
                translated_message=_clean_cell(row.get('translated_message')),
                chunks=_clean_cell(row.get('chunks')),
                sender=resolve_profile(row.get('sender')),
                receiver=resolve_profile(row.get('receiver')),
                session=target_session_id,
                created_at=_parse_created_at(row.get('created_at')),
                status=_clean_cell(row.get('status')),
                feedback=_clean_cell(row.get('feedback')),
                source=_clean_cell(row.get('source')) or CompanyChatSourceChoices.WEB,
                source_msg_id=_clean_cell(row.get('source_msg_id')),
                whatsapp_message_id=_clean_cell(row.get('whatsapp_message_id')),
                message_type=_clean_cell(row.get('message_type')),
                stage=_clean_cell(row.get('stage')),
                other_params=other_params,
                file_url=_clean_cell(row.get('file_url')),
            ))
        CompanyChat.objects.bulk_create(chat_objects)

        return {
            'file_session_id': file_session_id,
            'target_session_id': target_session_id,
            'status': 'collision' if collided else 'created',
            'message_count': len(chat_objects),
            'profiles_created': len(name_to_profile),
        }

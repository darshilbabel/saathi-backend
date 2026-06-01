import logging
import json_repair
from jinja2 import Template, UndefinedError
from chatbot.models import CompanyBotDynamicContextType
from chatbot.utils.sql_utils import run_sql_from_string, get_todays_date

logger = logging.getLogger('django')


class PromptBuilder:
    """Centralized prompt building logic"""

    @staticmethod
    def build_system_prompt(company_bot, state_machine=None, profile=None):
        system_parts = [company_bot.context.strip()]

        if state_machine and state_machine.context:
            system_parts.append(state_machine.context.strip())

        if state_machine and state_machine.completion_criteria:
            system_parts.append(f"Completion Criteria:\n{state_machine.completion_criteria.strip()}")

        rendered_tag_context = PromptBuilder._render_tag_context(company_bot, profile)
        if rendered_tag_context:
            system_parts.append(rendered_tag_context)

        dynamic_context = PromptBuilder._render_dynamic_context(company_bot)
        if dynamic_context:
            system_parts.append(dynamic_context)

        return "\n\n".join(system_parts)

    @staticmethod
    def _render_tag_context(company_bot, profile):
        tag_context = company_bot.tag_context
        if not tag_context or not tag_context.strip():
            return ""

        try:
            address = None
            if profile:
                address = profile.profile_address.all().first()

            context_data = {
                "profile": profile,
                "address": address,
            }

            return Template(tag_context).render(context_data).strip()
        except UndefinedError as e:
            logger.warning("tag_context template variable missing: %s", e)
            return tag_context.strip()
        except Exception as e:
            logger.error("Failed to render tag_context: %s", e, exc_info=True)
            return tag_context.strip()


    @staticmethod
    def _render_dynamic_context(company_bot):
        logger.info("[dynamic_context] type=%s has_value=%s", company_bot.dynamic_context_type if company_bot else None, bool(company_bot and company_bot.dynamic_context))
        if not company_bot or company_bot.dynamic_context_type != CompanyBotDynamicContextType.SQL_QUERY:
            return ""
        if not company_bot.dynamic_context:
            return ""

        try:
            raw = company_bot.dynamic_context
            if isinstance(raw, str):
                stripped = raw.strip()
                if stripped.startswith(('{', '[')):
                    parsed = json_repair.repair_json(raw, return_objects=True)
                else:
                    parsed = raw
            else:
                parsed = raw

            if isinstance(parsed, str):
                return run_sql_from_string(parsed).strip()

            if isinstance(parsed, dict) and parsed.get('prompt'):
                return run_sql_from_string(parsed['prompt']).strip()

            return get_todays_date(company_bot)
        except Exception as e:
            logger.error("Failed to render dynamic_context: %s", e, exc_info=True)
            return ""

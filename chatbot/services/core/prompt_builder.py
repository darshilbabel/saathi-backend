class PromptBuilder:
    """Centralized prompt building logic"""

    @staticmethod
    def build_system_prompt(company_bot, state_machine=None):
        system_parts = [company_bot.context.strip()]

        if state_machine and state_machine.context:
            system_parts.append(state_machine.context.strip())

        if state_machine and state_machine.completion_criteria:
            system_parts.append(f"Completion Criteria:\n{state_machine.completion_criteria.strip()}")

        return "\n\n".join(system_parts)

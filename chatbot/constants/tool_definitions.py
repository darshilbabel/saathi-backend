SEARCH_KNOWLEDGE_BASE_TOOL_NAME = "search_knowledge_base"

SEARCH_KNOWLEDGE_BASE_TOOL = {
    "type": "function",
    "function": {
        "name": SEARCH_KNOWLEDGE_BASE_TOOL_NAME,
        "description": (
            "Search the knowledge repository for relevant education content. Call this before "
            "answering knowledge queries, before generating MIP objectives, and before generating "
            "action steps. Extract 2–5 specific content keywords from the conversation as the "
            "query — avoid meta-words like 'discuss' or 'education query'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Specific content keywords to search for (e.g. 'FLN assessment Grade 2', "
                        "'community mobilisation enrollment', 'attendance recovery strategies'). "
                        "Keep it short and topic-focused."
                    ),
                }
            },
            "required": ["query"],
        },
    },
}

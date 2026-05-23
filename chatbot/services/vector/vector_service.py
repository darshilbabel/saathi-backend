import logging

from chatbot.utils.chat_query_handler import query_database

logger = logging.getLogger('django')


def _fetch_chunks(query, top_k, filter_score, priority):
    """Query vector DB and return chunks that meet the relevance threshold."""
    result = query_database(query_prompt=query, priority_filter=priority, limit=top_k)
    print("result: ", result)
    if not result or not result.get('results'):
        return []
    chunks = []
    for item in result['results']:
        score = item.get('score', 0)
        text = item.get('text', '')
        if text and len(text) > 20 and score >= filter_score:
            chunks.append({
                'text': text,
                'title': item.get('title', ''),
                'url': item.get('metadata', {}).get('url', ''),
            })
    return chunks


def fetch_context_for_query(query, company_bot):
    """Fetch relevant chunks and return (context_string, chunks_list)."""
    other = company_bot.other_params or {}
    priority = other.get('vector_priority', 'P1')
    chunks = _fetch_chunks(
        query=query, top_k=company_bot.top_k,
        filter_score=company_bot.filter_score, priority=priority,
    )
    if not chunks:
        logger.info('Vector service: no relevant chunks for query: %s', query)
        print(f'[vector_service] query="{query}" → 0 chunks found')
        no_result_context = ('\n\nNote: The knowledge service does not have relevant information for this query. '
                             'Answer based on your general knowledge or inform the user accordingly.')
        return no_result_context, []
    context = '\n\nRelevant context from knowledge base:\n' + ''.join(f'\n{c["text"]}' for c in chunks)
    return context, chunks

from chatbot.services.response_handlers.common_handler import CommonResponseHandler


class ResponseHandlerFactory:
    """Factory for creating response handlers"""

    _handlers = {
        'common': CommonResponseHandler,
    }

    @classmethod
    def create_handler(cls, handler_type):
        """Create response handler by type"""
        handler_class = cls._handlers.get(handler_type)
        if not handler_class:
            raise ValueError(f"Unknown handler type: {handler_type}")
        return handler_class()

    @classmethod
    def register_handler(cls, handler_type, handler_class):
        """Register new response handler"""
        cls._handlers[handler_type] = handler_class

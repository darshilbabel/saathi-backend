# Bot Strategies

## Overview

The chatbot's response-generation flow is implemented via a strategy pattern. All strategies inherit from the abstract `BotStrategy` base class located in `services/strategies/base_strategy.py`.

Only one strategy is registered/reachable today: every websocket session — regardless of bot type — connects through the single `ws/common/` route, and `AsyncSocketConsumer` hardcodes `bot_type='common'` on its call into `BotServiceFactory`. The `guest_discussion`/`guided_guest`/`oneshot` strategies (and their matching response handlers) were confirmed unreachable by any live code path and removed, along with `BotServiceFactory`'s registrations for them.

## Strategy Implemented

### CommonBotStrategy (only registered strategy)

The CommonBotStrategy is the sole strategy registered in `BotServiceFactory` and handles every chatbot flow.

- Uses the "common" response handler type (`CommonResponseHandler`, see [Services](chatbot_services.md)).
- Processes the session by retrieving the current state machine step associated with the chat session.
- Offers extensibility to support a wide range of chatbot flows without the need for separate custom strategies.

This structured approach keeps the strategy layer extensible — a new bot type can be added later by implementing `BotStrategy` and registering it via `BotServiceFactory.register_strategy(...)` — without requiring the previously-removed per-flow strategy classes.

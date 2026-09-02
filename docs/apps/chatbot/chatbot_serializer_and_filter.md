# Chatbot Serializers and Filters

## Overview

This document covers the serializers and filters modules within the chatbot application, which play crucial roles in data transformation, validation, and querying. Story/Media-specific serializers and filters (`media_serializer.py`, `story_serializer.py`, `media_filters.py`, `flow_filter.py`, `story_filter.py`) were removed along with those features — see the repo-root `CODE_CLEANUP_PLAN.md` for the full history.

## Serializers

Serializers handle the conversion between complex data types like Django models and JSON representations used in REST APIs. They also encapsulate input validation logic.

### Key Serializer Modules

- `base_serializer.py`: Base serializers providing common functionality (e.g. `ChatSessionSerializer`).
- `company_serializer.py`: Serializers for company-related data, including Flow language/connection-info serializers.
- `profile_serializer.py`: Handles profile, company chat, and company chat feedback serialization/validation.

## Filters

The filters in the chatbot are primarily used for filtering functionality within the Django admin interface, supporting admin users in querying and managing data efficiently.

### Key Filter Modules

- `admin_filter.py`: Provides core filtering capabilities customized for the admin panel.
- `drf_filter.py`: Contains filters used internally by DRF views.
- `custom_date_from_filter.py`: Provides specialized date filters for admin usage.

## Interaction

- Serializers handle API input/output data transformations and validations.
- Filters focus mainly on easing data management in admin UI by providing reusable constraints.

Together, serializers and filters establish reliable backend data handling and flexible admin querying capabilities.

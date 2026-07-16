import uuid

from django.core.cache import cache
from rest_framework.exceptions import ValidationError, PermissionDenied

class ChatState:
    # Redis is temporary workflow state, not permanent user-visible history.
    TTL_SECONDS = 30 * 60
    MAX_HISTORY_MESSAGES = 12
    MAX_MESSAGE_LENGTH = 2_000
    KEY_PREFIX = "ai:chat:"

    @classmethod
    def _key(cls, conversation_id):
        return f"{cls.KEY_PREFIX}{conversation_id}"

    @classmethod
    def create(cls, user=None):
        """
        Create a new chat conversation by generating a unique conversation ID and initializing its state.
        If a user is provided, associate the conversation with that user's ID.
        If no user is provided, the conversation will be treated as anonymous.
        """
        conversation_id = str(uuid.uuid4())
        state = {
            "user_id": user.id if user and user.is_authenticated else None,
            "selected_event_id": None,
            "history": []
        }
        cache.set(cls._key(conversation_id), state, timeout=cls.TTL_SECONDS)

        return conversation_id, state

    @classmethod
    def get(cls, conversation_id, user=None):
        """
        Retrieve the state of a conversation by its ID.
        If the conversation is associated with a user, ensure that the requesting user matches the stored user ID.
        If the conversation has expired or the user does not have permission, raise an appropriate exception.
        """
        try:
            conversation_id = str(uuid.UUID(str(conversation_id)))
        except (TypeError, ValueError):
            raise ValidationError({"conversation_id": "Invalid conversation ID."})

        state = cache.get(cls._key(conversation_id))
        if state is None:
            raise ValidationError(
                {"conversation_id": "Chat expired. Start a new conversation."}
            )

        if state["user_id"] is not None:
            if not user or not user.is_authenticated or state["user_id"] != user.id:
                raise PermissionDenied("This conversation belongs to another user.")

        # An anonymous visitor can continue after login, but an existing owner
        # must never be replaced by a different authenticated user.
        if user and user.is_authenticated and state["user_id"] is None:
            state["user_id"] = user.id

        cache.set(cls._key(conversation_id), state, timeout=cls.TTL_SECONDS)

        return conversation_id, state

    @classmethod
    def save(cls, conversation_id, state):
        cache.set(cls._key(conversation_id), state, timeout=cls.TTL_SECONDS)

    @classmethod
    def add_turn(cls, state, role, content):
        """
        Add a new turn to the conversation history.
        The turn consists of a role (e.g., "user" or "assistant") and the content of the message.
        The content is truncated to MAX_MESSAGE_LENGTH characters, and the history is limited to MAX_HISTORY_MESSAGES turns.
        """
        if not isinstance(content, str) or not content.strip():
            return

        state["history"].append(
            {
                "role": role,
                "content": content[:cls.MAX_MESSAGE_LENGTH],
            }
        )
        state["history"] = state["history"][-cls.MAX_HISTORY_MESSAGES:]

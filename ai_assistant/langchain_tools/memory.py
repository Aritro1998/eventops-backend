"""
Turns the conversation history stored between turns into the message
objects the chat model expects, and assembles the full list of messages
for one turn: the system instructions, everything said so far, and the
new message.
"""

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage


def history_to_messages(history: list[dict]) -> list[BaseMessage]:
    """
    Convert stored conversation turns into message objects.

    Each stored turn is a plain dict with a role ("user" or "assistant")
    and text content. This walks through them in order and creates the
    matching message object for each one.
    """
    converted = []

    for turn in history:
        if turn['role'] == 'user':
            converted.append(HumanMessage(content=turn['content']))
        elif turn['role'] == 'assistant':
            converted.append(AIMessage(content=turn['content']))

    return converted


def build_message_list(system_prompt: str, history: list[dict], user_prompt: str) -> list[BaseMessage]:
    """
    Build the full list of messages for one turn: the system
    instructions first, then every earlier turn in the conversation, and
    finally the new message the user just sent.
    """
    return [
        SystemMessage(content=system_prompt),
        *history_to_messages(history),
        HumanMessage(content=user_prompt),
    ]

from langchain_core.messages import (
    BaseMessage, 
    SystemMessage, 
    HumanMessage, 
    AIMessage
)

def history_to_messages(history: list[dict]) -> list[BaseMessage]:
    """Convert ChatState's stored history into LangChain message objects."""
    converted = []
    
    for turn in history:
        if turn['role'] == 'user':
            converted.append(HumanMessage(content=turn['content']))
        elif turn['role'] == 'assistant':
            converted.append(AIMessage(content=turn['content']))
        
    return converted


def build_message_list(system_prompt: str, history: list[dict], user_prompt: str) -> list[BaseMessage]:
    """
    LangChain messages instead of raw OpenAI-style dicts.
    """
    return [
        SystemMessage(content=system_prompt),
        *history_to_messages(history),
        HumanMessage(content=user_prompt),
    ]

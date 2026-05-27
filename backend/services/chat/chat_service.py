from langchain_core.messages import HumanMessage, AIMessage

_history: dict[str, list] = {}


def get_history(session_id: str) -> list:
    return _history.get(session_id, [])


def save_message(session_id: str, human: str, ai: str) -> None:
    if session_id not in _history:
        _history[session_id] = []
    _history[session_id].append(HumanMessage(content=human))
    _history[session_id].append(AIMessage(content=ai))

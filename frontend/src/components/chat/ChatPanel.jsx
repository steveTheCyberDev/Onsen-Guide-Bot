import ChatEmptyState from './ChatEmptyState';
import MessageList from './MessageList';
import TypingIndicator from './TypingIndicator';
import ChatInput from './ChatInput';
import { apiPost } from '../../services/api';

/**
 * ChatPanel — left panel wrapper.
 * Owns the /chat API call and dispatches CHAT_RESULTS / SET_STATUS.
 */
export default function ChatPanel({ state, dispatch }) {
  const { messages, status } = state;
  // Note: messages is read-only here — mutations go through dispatch
  const isLoading = status === 'loading';

  async function handleSend(text) {
    // 1. Append user message immediately so it renders without delay
    dispatch({ type: 'ADD_MESSAGE', payload: { role: 'user', content: text } });
    dispatch({ type: 'SET_STATUS', payload: 'loading' });

    try {
      const data = await apiPost('/chat', { message: text, session_id: 'default' });
      const onsens = data.onsens ?? [];
      const hotels = data.hotels ?? [];

      // 2. One atomic dispatch — appends assistant reply, sets onsens + hotels, resets selection.
      //    Reducer appends assistantMessage to current state.messages (no stale closure).
      //    When the reply includes hotels, the reducer also flips markers to 'both'.
      dispatch({
        type: 'CHAT_RESULTS',
        payload: {
          onsens,
          hotels,
          assistantMessage: { role: 'assistant', content: data.reply, onsens },
        },
      });
    } catch (err) {
      console.error('[ChatPanel] /chat error:', err);
      dispatch({
        type: 'ADD_MESSAGE',
        payload: { role: 'assistant', content: 'Something went wrong. Please try again.' },
      });
      dispatch({ type: 'SET_STATUS', payload: 'error' });
    }
  }

  const hasMessages = messages.length > 0;

  return (
    <div className="flex flex-col h-full">
      {/* Panel header */}
      <div className="shrink-0 px-4 py-3 border-b border-[#D9D0C5]">
        <h2 className="text-xs font-semibold text-[#6B6B6B] uppercase tracking-widest">
          Chat
        </h2>
      </div>

      {/* Message area or empty state */}
      {hasMessages ? (
        <>
          <MessageList messages={messages} />
          {isLoading && (
            <div className="px-3 pb-2">
              <TypingIndicator />
            </div>
          )}
        </>
      ) : (
        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="flex flex-col h-full items-center justify-center px-3 pb-4">
              <TypingIndicator />
            </div>
          ) : (
            <ChatEmptyState onSuggestionClick={handleSend} />
          )}
        </div>
      )}

      {/* Fixed input bar */}
      <ChatInput onSend={handleSend} disabled={isLoading} />
    </div>
  );
}

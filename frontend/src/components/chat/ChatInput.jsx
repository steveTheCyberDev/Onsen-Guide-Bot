import { useState, useRef, useEffect } from 'react';

/**
 * ChatInput — fixed input bar at the bottom of the chat panel.
 * Uses an auto-growing textarea: starts at 2 rows, grows up to 6 rows then
 * scrolls. Enter submits; Shift+Enter inserts a newline.
 */
export default function ChatInput({ onSend, disabled }) {
  const [value, setValue] = useState('');
  const textareaRef = useRef(null);

  // Auto-grow: reset height to 'auto' so scrollHeight reflects real content,
  // then cap at the line-height * MAX_ROWS equivalent.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    // clamp to roughly 6 lines (6 * 20px line-height + 16px vertical padding)
    const maxHeight = 6 * 20 + 16;
    el.style.height = Math.min(el.scrollHeight, maxHeight) + 'px';
  }, [value]);

  function handleSubmit(e) {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue('');
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      handleSubmit(e);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="shrink-0 flex gap-2 px-3 py-3 border-t border-[#D9D0C5] bg-[#F0EBE3]"
      aria-label="Send a message"
    >
      <label htmlFor="chat-input" className="sr-only">
        Ask about onsen in Japan
      </label>
      <textarea
        ref={textareaRef}
        id="chat-input"
        rows={2}
        className="flex-1 rounded-2xl border border-[#D9D0C5] bg-white px-4 py-2 text-sm text-[#2C2C2C] placeholder-[#A09A92] outline-none focus:border-[#C9533A] focus:ring-1 focus:ring-[#C9533A] transition-colors duration-150 disabled:opacity-50 resize-none overflow-y-auto leading-5"
        placeholder="Ask about onsen in Japan..."
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        aria-disabled={disabled}
        aria-label="Ask about onsen in Japan"
        autoComplete="off"
      />
      <button
        type="submit"
        disabled={disabled || !value.trim()}
        className="shrink-0 self-end rounded-full bg-[#C9533A] px-4 py-2 text-sm text-white font-medium hover:bg-[#b04730] transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-[#C9533A] focus:ring-offset-1"
        aria-label="Send message"
      >
        Send
      </button>
    </form>
  );
}

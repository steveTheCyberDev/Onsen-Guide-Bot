import { useState } from 'react';

/**
 * ChatInput — fixed input bar at the bottom of the chat panel.
 */
export default function ChatInput({ onSend, disabled }) {
  const [value, setValue] = useState('');

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
      <input
        id="chat-input"
        type="text"
        className="flex-1 rounded-full border border-[#D9D0C5] bg-white px-4 py-2 text-sm text-[#2C2C2C] placeholder-[#A09A92] outline-none focus:border-[#C9533A] focus:ring-1 focus:ring-[#C9533A] transition-colors duration-150 disabled:opacity-50"
        placeholder="Ask about onsen in Japan..."
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        aria-disabled={disabled}
        autoComplete="off"
      />
      <button
        type="submit"
        disabled={disabled || !value.trim()}
        className="shrink-0 rounded-full bg-[#C9533A] px-4 py-2 text-sm text-white font-medium hover:bg-[#b04730] transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-[#C9533A] focus:ring-offset-1"
        aria-label="Send message"
      >
        Send
      </button>
    </form>
  );
}

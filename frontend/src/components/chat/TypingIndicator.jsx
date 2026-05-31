/**
 * TypingIndicator — three animated dots shown while the agent is processing.
 */
export default function TypingIndicator() {
  return (
    <div className="flex justify-start" aria-live="polite" aria-label="Agent is typing">
      <div className="flex items-center gap-1 bg-white rounded-2xl px-4 py-3 shadow-sm border border-[#E8E0D5]">
        <span
          className="w-2 h-2 rounded-full bg-[#C9533A] opacity-60 animate-bounce"
          style={{ animationDelay: '0ms' }}
          aria-hidden="true"
        />
        <span
          className="w-2 h-2 rounded-full bg-[#C9533A] opacity-60 animate-bounce"
          style={{ animationDelay: '150ms' }}
          aria-hidden="true"
        />
        <span
          className="w-2 h-2 rounded-full bg-[#C9533A] opacity-60 animate-bounce"
          style={{ animationDelay: '300ms' }}
          aria-hidden="true"
        />
      </div>
    </div>
  );
}

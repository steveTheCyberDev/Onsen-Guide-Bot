const SUGGESTIONS = [
  'Find onsen in Okinawa',
  'Find onsen in Shizuoka'
];

export default function ChatEmptyState({ onSuggestionClick }) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-4 pb-6 text-center">
      <div className="text-4xl mb-3" aria-hidden="true">♨️</div>
      <h2
        className="font-serif text-base font-semibold text-[#2C2C2C] mb-1"
      >
        Find your perfect onsen
      </h2>
      <p className="text-xs text-[#6B6B6B] mb-5 leading-relaxed">
        Ask me anything about Japanese hot springs in English.
      </p>
      <ul className="w-full space-y-2" aria-label="Suggested questions">
        {SUGGESTIONS.map((s) => (
          <li key={s}>
            <button
              onClick={() => onSuggestionClick(s)}
              className="w-full text-left px-3 py-2 rounded-lg bg-white border border-[#D9D0C5] text-xs text-[#2C2C2C] hover:border-[#C9533A] hover:bg-[#FAF7F2] transition-colors duration-150 focus-ring"
            >
              {s}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

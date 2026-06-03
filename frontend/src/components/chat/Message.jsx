import OnsenMiniCard from './OnsenMiniCard';

/**
 * Message — individual message bubble.
 * role: 'user' | 'assistant'
 * content: string
 * onsens: optional array of onsen objects attached to this assistant message
 * onSelectOnsen: optional callback(onsen) fired when user clicks an OnsenMiniCard
 */
export default function Message({ role, content, onsens, onSelectOnsen }) {
  const isUser = role === 'user';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[88%] rounded-2xl px-3 py-2 text-sm leading-relaxed ${
          isUser
            ? 'bg-[#C9533A] text-white rounded-br-sm'
            : 'bg-white text-[#2C2C2C] border border-[#E8E0D5] rounded-bl-sm shadow-sm'
        }`}
      >
        <p className="whitespace-pre-wrap">{content}</p>
        {!isUser && onsens && onsens.length > 0 && (
          <ul className="mt-2 space-y-2" aria-label={`${onsens.length} onsen results`}>
            {onsens.map((onsen, i) => (
              <li key={onsen.name ?? i}>
                <OnsenMiniCard onsen={onsen} onSelect={onSelectOnsen} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

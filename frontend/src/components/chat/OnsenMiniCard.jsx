/**
 * OnsenMiniCard — compact onsen card rendered inline inside an assistant message bubble.
 * Receives an onsen object: { name, location, spring_type, spa_quality, pros, cons }
 *
 * pros/cons are string[] populated only in recommend mode with analyze enabled.
 * Empty/absent in search mode — the card renders identically to before in that case.
 *
 * When onSelect is provided the card is rendered as a focusable button so the
 * user can click/keyboard-activate it to centre that marker on the map.
 * Cards for onsens without lat/lng still render but onSelect is a no-op so the
 * map is not panned to a missing position.
 */
export default function OnsenMiniCard({ onsen, onSelect }) {
  if (!onsen) return null;

  const hasCoords = Boolean(onsen.lat && onsen.lng);
  const pros = onsen.pros ?? [];
  const cons = onsen.cons ?? [];
  const hasProsOrCons = pros.length > 0 || cons.length > 0;

  function handleSelect() {
    if (onSelect && hasCoords) {
      onSelect(onsen);
    }
  }

  const isInteractive = Boolean(onSelect);

  const innerContent = (
    <>
      <div className="flex items-center gap-1">
        <span className="text-sm" aria-hidden="true">♨️</span>
        <span className="font-semibold text-[#C9533A] truncate">{onsen.name}</span>
      </div>
      {onsen.location && (
        <div className="flex items-center gap-1 text-[#6B6B6B]">
          <svg
            className="w-3 h-3 shrink-0"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"
            />
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"
            />
          </svg>
          <span className="truncate">{onsen.location}</span>
        </div>
      )}
      {onsen.spring_type && (
        <div className="text-[#6B6B6B]">
          <span className="font-medium">Type:</span> {onsen.spring_type}
        </div>
      )}
      {onsen.spa_quality && (
        <p className="text-[#6B6B6B] leading-relaxed line-clamp-2">{onsen.spa_quality}</p>
      )}
      {hasProsOrCons && (
        <div className="mt-1 grid grid-cols-1 gap-1.5 sm:grid-cols-2" data-testid="onsen-pros-cons">
          {pros.length > 0 && (
            <ul aria-label={`${onsen.name} pros`} className="space-y-0.5">
              {pros.map((pro, i) => (
                <li key={i} className="flex items-start gap-1 text-[#2D6A4F]">
                  <span aria-hidden="true" className="mt-px shrink-0">✓</span>
                  <span className="leading-snug">{pro}</span>
                </li>
              ))}
            </ul>
          )}
          {cons.length > 0 && (
            <ul aria-label={`${onsen.name} cons`} className="space-y-0.5">
              {cons.map((con, i) => (
                <li key={i} className="flex items-start gap-1 text-[#C9533A]">
                  <span aria-hidden="true" className="mt-px shrink-0">✕</span>
                  <span className="leading-snug">{con}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );

  if (isInteractive) {
    return (
      <button
        type="button"
        onClick={handleSelect}
        aria-label={`Show ${onsen.name} on map`}
        className="onsen-mini-card focus-ring w-full text-left transition-colors duration-150 hover:border-[#C9533A] hover:bg-[#FDF5F3] cursor-pointer"
      >
        {innerContent}
      </button>
    );
  }

  return (
    <div className="onsen-mini-card">
      {innerContent}
    </div>
  );
}

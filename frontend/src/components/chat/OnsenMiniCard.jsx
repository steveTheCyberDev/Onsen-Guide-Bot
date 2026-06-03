/**
 * OnsenMiniCard — compact onsen card rendered inline inside an assistant message bubble.
 * Receives an onsen object: { name, location, spring_type, spa_quality, sales_point }
 *
 * When onSelect is provided the card is rendered as a focusable button so the
 * user can click/keyboard-activate it to centre that marker on the map.
 * Cards for onsens without lat/lng still render but onSelect is a no-op so the
 * map is not panned to a missing position.
 */
export default function OnsenMiniCard({ onsen, onSelect }) {
  if (!onsen) return null;

  const hasCoords = Boolean(onsen.lat && onsen.lng);

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
        <div className="text-[#6B6B6B]">
          <span className="font-medium">Quality:</span> {onsen.spa_quality}
        </div>
      )}
      {onsen.sales_point && (
        <p className="text-[#6B6B6B] leading-relaxed line-clamp-2">{onsen.sales_point}</p>
      )}
    </>
  );

  if (isInteractive) {
    return (
      <button
        type="button"
        onClick={handleSelect}
        aria-label={`Show ${onsen.name} on map`}
        className="w-full text-left mt-2 rounded-lg border border-[#D9D0C5] bg-[#FAF7F2] px-3 py-2 text-xs text-[#2C2C2C] space-y-1 transition-colors duration-150 hover:border-[#C9533A] hover:bg-[#FDF5F3] focus:outline-none focus:ring-2 focus:ring-[#C9533A] focus:ring-offset-1 cursor-pointer"
      >
        {innerContent}
      </button>
    );
  }

  return (
    <div className="mt-2 rounded-lg border border-[#D9D0C5] bg-[#FAF7F2] px-3 py-2 text-xs text-[#2C2C2C] space-y-1">
      {innerContent}
    </div>
  );
}

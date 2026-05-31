/**
 * OnsenInfoStrip — slim info strip pinned to the bottom of the map panel.
 * Appears when selectedOnsen is set (HOVER_ONSEN). NOT a popup — sits below the map.
 * Provides "See nearby hotels" button to trigger /hotels fetch.
 */
export default function OnsenInfoStrip({ onsen, onSeeHotels, onClose }) {
  if (!onsen) return null;

  return (
    <div
      className="absolute bottom-0 left-0 right-0 z-10 flex items-center justify-between px-4 py-3 border-t border-[#D9D0C5] animate-slide-up"
      style={{ background: 'rgba(250,247,242,0.96)', backdropFilter: 'blur(4px)' }}
      role="region"
      aria-label={`Onsen information: ${onsen.name}`}
    >
      {/* Left: onsen details */}
      <div className="flex-1 min-w-0 mr-4">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-base" aria-hidden="true">♨️</span>
          <span className="font-semibold text-sm text-[#C9533A] truncate">{onsen.name}</span>
        </div>
        <div className="flex items-center gap-3 text-xs text-[#6B6B6B] flex-wrap">
          {onsen.spring_type && (
            <span>{onsen.spring_type}</span>
          )}
          {onsen.spa_quality && (
            <>
              <span className="text-[#D9D0C5]" aria-hidden="true">·</span>
              <span>{onsen.spa_quality}</span>
            </>
          )}
          {onsen.location && (
            <>
              <span className="text-[#D9D0C5]" aria-hidden="true">·</span>
              <span className="truncate">{onsen.location}</span>
            </>
          )}
        </div>
      </div>

      {/* Right: actions */}
      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={() => onSeeHotels(onsen)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#C9533A] text-white text-xs font-medium hover:bg-[#b04730] transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-[#C9533A] focus:ring-offset-1"
          aria-label={`See nearby hotels for ${onsen.name}`}
        >
          <span aria-hidden="true">🏨</span>
          See nearby hotels
        </button>
        <button
          onClick={onClose}
          className="p-1 rounded-full text-[#6B6B6B] hover:bg-[#E8E0D5] transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-[#C9533A]"
          aria-label="Close onsen info strip"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  );
}

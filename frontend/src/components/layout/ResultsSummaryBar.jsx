/**
 * ResultsSummaryBar — "Showing 3 onsen in Okinawa" + reset button
 * Rendered inside MapPanel above the map or below the header strip.
 */
export default function ResultsSummaryBar({ onsens, selectedPrefecture, onReset }) {
  if (!onsens || onsens.length === 0) return null;

  const locationLabel = selectedPrefecture?.value ?? 'Japan';

  return (
    <div className="flex items-center justify-between px-4 py-2 bg-[rgba(250,247,242,0.95)] border-b border-[#E8E0D5] text-sm text-[#2C2C2C] z-10">
      <span>
        Showing{' '}
        <strong className="font-semibold text-[#C9533A]">{onsens.length}</strong>{' '}
        onsen in{' '}
        <strong className="font-semibold">{locationLabel}</strong>
      </span>
      <button
        onClick={onReset}
        className="ml-4 px-3 py-1 rounded-full border border-[#C9533A] text-[#C9533A] text-xs font-medium hover:bg-[#C9533A] hover:text-white transition-colors duration-150 focus-ring"
        aria-label="Reset search results"
      >
        Reset
      </button>
    </div>
  );
}

/**
 * HotelPanelEmpty — shown before any hotel data is loaded.
 * Includes a Japanese bathing etiquette tip.
 */
export default function HotelPanelEmpty() {
  return (
    <div className="flex flex-col items-center justify-center h-full px-5 pb-8 text-center">
      <div className="text-5xl mb-4" aria-hidden="true">♨️</div>
      <p className="font-serif text-sm font-semibold text-[#2C2C2C] mb-2">
        Discover nearby hotels
      </p>
      <p className="text-xs text-[#6B6B6B] leading-relaxed mb-6">
        Hover over an onsen marker on the map, then click{' '}
        <strong className="text-[#C9533A]">See nearby hotels</strong> to find accommodation.
      </p>

      {/* Etiquette tip */}
      <div className="w-full rounded-xl bg-white border border-[#D9D0C5] px-4 py-3 text-left">
        <p className="text-xs font-semibold text-[#2D6A4F] mb-1 uppercase tracking-wide">
          Onsen Etiquette Tip
        </p>
        <p className="text-xs text-[#6B6B6B] leading-relaxed">
          Always wash thoroughly at the shower stations before entering the communal bath.
          Towels must not touch the water — fold them on your head or leave them at the edge.
        </p>
      </div>
    </div>
  );
}

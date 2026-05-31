import { useState } from 'react';

const PREFECTURES = [
  { label: 'Okinawa 沖縄', value: 'Okinawa', lat: 26.2124, lng: 127.6809 },
  { label: 'Hokkaido 北海道', value: 'Hokkaido', lat: 43.0642, lng: 141.3469 },
  { label: 'Tokyo 東京', value: 'Tokyo', lat: 35.6762, lng: 139.6503 },
  { label: 'Kyoto 京都', value: 'Kyoto', lat: 35.0116, lng: 135.7681 },
  { label: 'Hakone 箱根', value: 'Hakone', lat: 35.2327, lng: 139.1069 },
  { label: 'Beppu 別府', value: 'Beppu', lat: 33.2846, lng: 131.4908 },
  { label: 'Kusatsu 草津', value: 'Kusatsu', lat: 36.6196, lng: 138.5963 },
  { label: 'Nikko 日光', value: 'Nikko', lat: 36.7199, lng: 139.6983 },
  { label: 'Nagano 長野', value: 'Nagano', lat: 36.6485, lng: 138.1948 },
  { label: 'Kagoshima 鹿児島', value: 'Kagoshima', lat: 31.5966, lng: 130.5571 },
];

export default function Header({ selectedPrefecture, onSelectPrefecture }) {
  const [isOpen, setIsOpen] = useState(false);

  function handleSelect(prefecture) {
    onSelectPrefecture(prefecture);
    setIsOpen(false);
  }

  const current = PREFECTURES.find((p) => p.value === selectedPrefecture?.value);

  return (
    <header
      className="flex items-center justify-between px-6 bg-[#FAF7F2] border-b border-[#E8E0D5] z-20"
      style={{ height: '64px', minHeight: '64px', maxHeight: '64px' }}
    >
      {/* Title */}
      <div className="flex items-center gap-2">
        <span className="text-2xl" aria-hidden="true">🌸</span>
        <h1 className="font-serif text-xl font-semibold text-[#2C2C2C] tracking-tight">
          Onsen Guide
        </h1>
        <span
          className="text-sm text-[#C9533A] font-medium ml-1 opacity-70"
          style={{ fontFamily: "'Noto Sans JP', sans-serif" }}
          aria-label="onsen in Japanese"
        >
          温泉
        </span>
      </div>

      {/* Prefecture Dropdown */}
      <div className="relative">
        <button
          onClick={() => setIsOpen((v) => !v)}
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          aria-label="Select prefecture"
          className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#D9D0C5] bg-white text-sm text-[#2C2C2C] hover:border-[#C9533A] transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-[#C9533A] focus:ring-offset-1"
        >
          <span>{current ? current.label : 'All Japan 日本'}</span>
          <svg
            className={`w-4 h-4 text-[#C9533A] transition-transform duration-150 ${isOpen ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {isOpen && (
          <ul
            role="listbox"
            aria-label="Prefecture options"
            className="absolute right-0 mt-1 w-52 bg-white border border-[#D9D0C5] rounded-lg shadow-lg z-50 py-1 overflow-hidden"
          >
            <li
              role="option"
              aria-selected={!selectedPrefecture}
              onClick={() => handleSelect(null)}
              className="px-4 py-2 text-sm cursor-pointer hover:bg-[#FAF7F2] text-[#2C2C2C]"
            >
              All Japan 日本
            </li>
            {PREFECTURES.map((p) => (
              <li
                key={p.value}
                role="option"
                aria-selected={selectedPrefecture?.value === p.value}
                onClick={() => handleSelect(p)}
                className={`px-4 py-2 text-sm cursor-pointer hover:bg-[#FAF7F2] text-[#2C2C2C] ${
                  selectedPrefecture?.value === p.value ? 'bg-[#F0EBE3] font-medium' : ''
                }`}
              >
                {p.label}
              </li>
            ))}
          </ul>
        )}
      </div>
    </header>
  );
}

export { PREFECTURES };

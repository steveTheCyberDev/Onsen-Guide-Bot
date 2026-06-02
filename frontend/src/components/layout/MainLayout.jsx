/**
 * MainLayout — 3-panel grid wrapper
 * Proportions: Chat 22% / Map 52% / Hotels 26%
 * Desktop only for V1.
 */
export default function MainLayout({ chatPanel, mapPanel, hotelPanel }) {
  return (
    <div
      className="flex overflow-hidden"
      style={{ height: 'calc(100vh - 64px)' }}
    >
      {/* Chat — 22% */}
      <aside
        className="flex flex-col bg-[#F0EBE3] border-r border-[#E8E0D5] overflow-hidden"
        style={{ width: '22%', minWidth: '220px' }}
        aria-label="Chat panel"
      >
        {chatPanel}
      </aside>

      {/* Map — 52% */}
      <main
        className="relative flex flex-col overflow-hidden"
        style={{ width: '52%' }}
        aria-label="Map panel"
      >
        {mapPanel}
      </main>

      {/* Hotels — 26% */}
      <aside
        className="flex flex-col bg-[#FAF7F2] border-l border-[#E8E0D5] overflow-hidden"
        style={{ width: '26%', minWidth: '260px' }}
        aria-label="Hotel panel"
      >
        {hotelPanel}
      </aside>
    </div>
  );
}

import Chat from "./components/Chat";

export default function App() {
  return (
    <div className="min-h-screen bg-[#FAF7F2] font-sans">
      <header className="text-center py-6">
        <h1 className="text-2xl font-semibold text-[#C45C3A]">温泉 Onsen Guide</h1>
        <p className="text-sm text-gray-500 mt-1">Find your perfect Japanese hot spring — in English</p>
      </header>
      <main className="max-w-2xl mx-auto">
        <Chat />
      </main>
    </div>
  );
}

import { useReducer } from 'react';
import { appReducer, initialState } from './reducer/appReducer';
import Header from './components/layout/Header';
import MainLayout from './components/layout/MainLayout';
import ChatPanel from './components/chat/ChatPanel';
import MapPanel from './components/map/MapPanel';
import HotelPanel from './components/hotels/HotelPanel';

export default function App() {
  const [state, dispatch] = useReducer(appReducer, initialState);

  function handleSelectPrefecture(prefecture) {
    dispatch({ type: 'SELECT_PREFECTURE', payload: prefecture });
  }

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-[#FAF7F2]">
      <Header
        selectedPrefecture={state.selectedPrefecture}
        onSelectPrefecture={handleSelectPrefecture}
      />
      <MainLayout
        chatPanel={<ChatPanel state={state} dispatch={dispatch} />}
        mapPanel={<MapPanel state={state} dispatch={dispatch} />}
        hotelPanel={<HotelPanel state={state} dispatch={dispatch} />}
      />
    </div>
  );
}

export const initialState = {
  onsens: [],
  hotels: [],
  messages: [],
  selectedOnsen: null,
  selectedHotel: null,
  activeMarkers: 'onsens', // 'onsens' | 'both'
  selectedPrefecture: null,
  status: 'idle', // 'idle' | 'loading' | 'error'
};

export function appReducer(state, action) {
  switch (action.type) {
    case 'CHAT_RESULTS':
      // payload: { onsens, assistantMessage }
      // Appends the assistant reply to existing messages — no stale closure risk.
      return {
        ...state,
        onsens: action.payload.onsens ?? [],
        messages: action.payload.assistantMessage
          ? [...state.messages, action.payload.assistantMessage]
          : state.messages,
        hotels: [],
        selectedOnsen: null,
        selectedHotel: null,
        activeMarkers: 'onsens',
        status: 'idle',
      };

    case 'ADD_MESSAGE':
      return {
        ...state,
        messages: [...state.messages, action.payload],
      };

    case 'HOVER_ONSEN':
      return {
        ...state,
        selectedOnsen: action.payload,
      };

    case 'SHOW_HOTELS':
      return {
        ...state,
        hotels: action.payload ?? [],
        activeMarkers: 'both',
        status: 'idle',
      };

    case 'SELECT_HOTEL':
      return {
        ...state,
        selectedHotel: action.payload,
      };

    case 'SELECT_PREFECTURE':
      return {
        ...state,
        selectedPrefecture: action.payload,
        onsens: [],
        hotels: [],
        selectedOnsen: null,
        selectedHotel: null,
        activeMarkers: 'onsens',
        messages: [],
        status: 'idle',
      };

    case 'SET_STATUS':
      return {
        ...state,
        status: action.payload,
      };

    case 'RESET':
      return {
        ...initialState,
      };

    default:
      return state;
  }
}

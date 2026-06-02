import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MapPanel from './MapPanel';
import { initialState } from '../../reducer/appReducer';

// ---------------------------------------------------------------------------
// Mock @react-google-maps/api
//
// The default mock (used by most tests) has isLoaded=true so MapPanel renders
// the map body. Individual tests that need a different value for isLoaded or
// loadError call vi.mocked(useJsApiLoader).mockReturnValueOnce(...) before
// rendering.
// ---------------------------------------------------------------------------

vi.mock('@react-google-maps/api', () => {
  const useJsApiLoader = vi.fn(() => ({ isLoaded: true, loadError: null }));

  // GoogleMap renders its children inside a plain div.
  const GoogleMap = vi.fn(({ children, onLoad }) => {
    // Call onLoad with a minimal mapRef-like object so MapPanel's useCallback
    // can store it without crashing.
    if (onLoad) {
      onLoad({
        fitBounds: vi.fn(),
        panTo: vi.fn(),
        setZoom: vi.fn(),
      });
    }
    return <div data-testid="google-map">{children}</div>;
  });

  // OverlayView renders its children directly so marker buttons are in the DOM.
  const OverlayView = vi.fn(({ children }) => <>{children}</>);
  OverlayView.OVERLAY_MOUSE_TARGET = 'overlayMouseTarget';

  return { useJsApiLoader, GoogleMap, OverlayView };
});

// Import the mock so tests can override its return value per-test.
import { useJsApiLoader } from '@react-google-maps/api';

// ---------------------------------------------------------------------------
// Stub window.google.maps
//
// MapPanel's useEffect calls `new window.google.maps.LatLngBounds()` whenever
// onsens change. jsdom has no Google Maps runtime, so we need a minimal stub.
// We attach it before each test and clean up after.
// ---------------------------------------------------------------------------

function makeGoogleStub() {
  const bounds = {
    extend: vi.fn(),
    isEmpty: vi.fn(() => true), // returning true prevents fitBounds calls on the mapRef
  };
  return {
    maps: {
      LatLngBounds: vi.fn(() => bounds),
    },
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FAKE_API_URL = 'http://localhost:8000';

function makeState(overrides = {}) {
  return { ...initialState, ...overrides };
}

function makeFetchOk(body) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
  });
}

function makeFetchNotOk(status = 500) {
  return Promise.resolve({
    ok: false,
    status,
    json: () => Promise.resolve({}),
  });
}

const ONSEN_WITH_COORDS = { name: 'Yamada Onsen', lat: 26.2, lng: 127.6, spring_type: 'Sodium' };
const ONSEN_NO_COORDS = { name: 'Ghost Onsen' }; // no lat/lng
const HOTEL = { name: 'Coral Hotel', lat: 26.22, lng: 127.7 };

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('MapPanel', () => {
  let dispatch;

  beforeEach(() => {
    dispatch = vi.fn();
    vi.stubGlobal('fetch', vi.fn());
    // Provide a minimal window.google stub so MapPanel's useEffect (which calls
    // new window.google.maps.LatLngBounds()) doesn't throw in jsdom.
    vi.stubGlobal('google', makeGoogleStub());
    // Reset useJsApiLoader to the default loaded state before each test.
    vi.mocked(useJsApiLoader).mockReturnValue({ isLoaded: true, loadError: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  // -------------------------------------------------------------------------
  // Load / error states
  // -------------------------------------------------------------------------

  describe('map loading states', () => {
    it('renders the loading spinner when isLoaded is false', () => {
      vi.mocked(useJsApiLoader).mockReturnValue({ isLoaded: false, loadError: null });
      render(<MapPanel state={makeState()} dispatch={dispatch} />);
      expect(screen.getByRole('status', { name: /loading map/i })).toBeInTheDocument();
      expect(screen.getByText(/loading map/i)).toBeInTheDocument();
    });

    it('renders the map-failed message when loadError is truthy', () => {
      vi.mocked(useJsApiLoader).mockReturnValue({
        isLoaded: false,
        loadError: new Error('API key invalid'),
      });
      render(<MapPanel state={makeState()} dispatch={dispatch} />);
      expect(screen.getByText(/Map failed to load/i)).toBeInTheDocument();
      expect(screen.getByText('VITE_GOOGLE_MAPS_API_KEY')).toBeInTheDocument();
    });

    it('renders the GoogleMap container when isLoaded is true', () => {
      render(<MapPanel state={makeState()} dispatch={dispatch} />);
      expect(screen.getByTestId('google-map')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Empty state overlay
  // -------------------------------------------------------------------------

  describe('empty state overlay', () => {
    it('shows the search prompt when there are no onsens and status is idle', () => {
      render(<MapPanel state={makeState()} dispatch={dispatch} />);
      expect(
        screen.getByText(/Search for onsen in the chat to see them on the map/i)
      ).toBeInTheDocument();
    });

    it('hides the search prompt when onsens are present', () => {
      render(
        <MapPanel
          state={makeState({ onsens: [ONSEN_WITH_COORDS] })}
          dispatch={dispatch}
        />
      );
      expect(
        screen.queryByText(/Search for onsen in the chat to see them on the map/i)
      ).not.toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Onsen markers
  // -------------------------------------------------------------------------

  describe('onsen markers', () => {
    it('renders an OnsenMarker button for each onsen with valid coords', () => {
      render(
        <MapPanel
          state={makeState({ onsens: [ONSEN_WITH_COORDS] })}
          dispatch={dispatch}
        />
      );
      expect(
        screen.getByRole('button', { name: `Onsen: ${ONSEN_WITH_COORDS.name}` })
      ).toBeInTheDocument();
    });

    it('does not render a marker button for onsens lacking lat/lng', () => {
      render(
        <MapPanel
          state={makeState({ onsens: [ONSEN_NO_COORDS] })}
          dispatch={dispatch}
        />
      );
      expect(
        screen.queryByRole('button', { name: `Onsen: ${ONSEN_NO_COORDS.name}` })
      ).not.toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Hotel markers — gated on activeMarkers === 'both'
  // -------------------------------------------------------------------------

  describe('hotel markers', () => {
    it('renders hotel markers when activeMarkers is "both"', () => {
      render(
        <MapPanel
          state={makeState({
            onsens: [ONSEN_WITH_COORDS],
            hotels: [HOTEL],
            activeMarkers: 'both',
          })}
          dispatch={dispatch}
        />
      );
      expect(
        screen.getByRole('button', { name: `Hotel: ${HOTEL.name}` })
      ).toBeInTheDocument();
    });

    it('does NOT render hotel markers when activeMarkers is "onsens"', () => {
      render(
        <MapPanel
          state={makeState({
            onsens: [ONSEN_WITH_COORDS],
            hotels: [HOTEL],
            activeMarkers: 'onsens', // default
          })}
          dispatch={dispatch}
        />
      );
      expect(
        screen.queryByRole('button', { name: `Hotel: ${HOTEL.name}` })
      ).not.toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // handleSeeHotels — happy path
  // -------------------------------------------------------------------------

  describe('handleSeeHotels — happy path', () => {
    it('posts to /hotels with latitude, longitude, and radius=3', async () => {
      const returnedHotels = [HOTEL];
      fetch.mockReturnValueOnce(makeFetchOk({ hotels: returnedHotels }));

      // Render with a selected onsen so OnsenInfoStrip appears.
      render(
        <MapPanel
          state={makeState({ selectedOnsen: ONSEN_WITH_COORDS })}
          dispatch={dispatch}
        />
      );

      const user = userEvent.setup();
      const btn = screen.getByRole('button', {
        name: `See nearby hotels for ${ONSEN_WITH_COORDS.name}`,
      });
      await user.click(btn);

      expect(fetch).toHaveBeenCalledWith(
        `${FAKE_API_URL}/hotels`,
        expect.objectContaining({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            latitude: ONSEN_WITH_COORDS.lat,
            longitude: ONSEN_WITH_COORDS.lng,
            radius: 3,
          }),
        })
      );
    });

    it('dispatches SET_STATUS loading then SHOW_HOTELS with returned hotels', async () => {
      const returnedHotels = [HOTEL];
      fetch.mockReturnValueOnce(makeFetchOk({ hotels: returnedHotels }));

      const user = userEvent.setup();
      render(
        <MapPanel
          state={makeState({ selectedOnsen: ONSEN_WITH_COORDS })}
          dispatch={dispatch}
        />
      );

      await user.click(
        screen.getByRole('button', {
          name: `See nearby hotels for ${ONSEN_WITH_COORDS.name}`,
        })
      );

      // SET_STATUS loading fires before the await
      expect(dispatch).toHaveBeenCalledWith({ type: 'SET_STATUS', payload: 'loading' });

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith({
          type: 'SHOW_HOTELS',
          payload: returnedHotels,
        });
      });
    });

    it('defaults to an empty hotels array when response omits the hotels key', async () => {
      fetch.mockReturnValueOnce(makeFetchOk({})); // no hotels key

      const user = userEvent.setup();
      render(
        <MapPanel
          state={makeState({ selectedOnsen: ONSEN_WITH_COORDS })}
          dispatch={dispatch}
        />
      );

      await user.click(
        screen.getByRole('button', {
          name: `See nearby hotels for ${ONSEN_WITH_COORDS.name}`,
        })
      );

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith({ type: 'SHOW_HOTELS', payload: [] });
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleSeeHotels — guard: onsen without lat/lng
  // -------------------------------------------------------------------------

  describe('handleSeeHotels — guard (no coords)', () => {
    it('does not call fetch when the selected onsen has no lat/lng', async () => {
      // Manually call handleSeeHotels by rendering with an onsen that has coords
      // but then providing ONSEN_NO_COORDS as selectedOnsen so the strip renders,
      // and clicking "See nearby hotels" should do nothing.
      //
      // OnsenInfoStrip renders only when selectedOnsen is truthy. ONSEN_NO_COORDS
      // is truthy (it has a name), but its lat/lng are undefined — the guard
      // inside handleSeeHotels should bail out without fetching.
      render(
        <MapPanel
          state={makeState({ selectedOnsen: ONSEN_NO_COORDS })}
          dispatch={dispatch}
        />
      );

      const user = userEvent.setup();
      const btn = screen.getByRole('button', {
        name: `See nearby hotels for ${ONSEN_NO_COORDS.name}`,
      });
      await user.click(btn);

      expect(fetch).not.toHaveBeenCalled();
    });
  });

  // -------------------------------------------------------------------------
  // handleSeeHotels — error path
  // -------------------------------------------------------------------------

  describe('handleSeeHotels — error path', () => {
    beforeEach(() => {
      // Silence the console.error that MapPanel logs in its catch block.
      vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    it('dispatches SHOW_HOTELS [] and SET_STATUS error when fetch rejects', async () => {
      fetch.mockRejectedValueOnce(new Error('Network failure'));

      const user = userEvent.setup();
      render(
        <MapPanel
          state={makeState({ selectedOnsen: ONSEN_WITH_COORDS })}
          dispatch={dispatch}
        />
      );

      await user.click(
        screen.getByRole('button', {
          name: `See nearby hotels for ${ONSEN_WITH_COORDS.name}`,
        })
      );

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith({ type: 'SHOW_HOTELS', payload: [] });
        expect(dispatch).toHaveBeenCalledWith({ type: 'SET_STATUS', payload: 'error' });
      });
    });

    it('dispatches SHOW_HOTELS [] and SET_STATUS error when fetch returns non-ok', async () => {
      fetch.mockReturnValueOnce(makeFetchNotOk(502));

      const user = userEvent.setup();
      render(
        <MapPanel
          state={makeState({ selectedOnsen: ONSEN_WITH_COORDS })}
          dispatch={dispatch}
        />
      );

      await user.click(
        screen.getByRole('button', {
          name: `See nearby hotels for ${ONSEN_WITH_COORDS.name}`,
        })
      );

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith({ type: 'SHOW_HOTELS', payload: [] });
        expect(dispatch).toHaveBeenCalledWith({ type: 'SET_STATUS', payload: 'error' });
      });
    });

    it('does NOT dispatch SHOW_HOTELS with real hotels on error', async () => {
      fetch.mockRejectedValueOnce(new Error('fail'));

      const user = userEvent.setup();
      render(
        <MapPanel
          state={makeState({ selectedOnsen: ONSEN_WITH_COORDS })}
          dispatch={dispatch}
        />
      );

      await user.click(
        screen.getByRole('button', {
          name: `See nearby hotels for ${ONSEN_WITH_COORDS.name}`,
        })
      );

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith({ type: 'SET_STATUS', payload: 'error' });
      });

      // SHOW_HOTELS should have been dispatched only once, with an empty array.
      const showHotelsCalls = dispatch.mock.calls.filter(
        ([action]) => action.type === 'SHOW_HOTELS'
      );
      expect(showHotelsCalls).toHaveLength(1);
      expect(showHotelsCalls[0][0].payload).toEqual([]);
    });
  });

  // -------------------------------------------------------------------------
  // OnsenInfoStrip integration
  // -------------------------------------------------------------------------

  describe('OnsenInfoStrip integration', () => {
    it('shows the info strip region when selectedOnsen is set', () => {
      render(
        <MapPanel
          state={makeState({ selectedOnsen: ONSEN_WITH_COORDS })}
          dispatch={dispatch}
        />
      );
      expect(
        screen.getByRole('region', {
          name: `Onsen information: ${ONSEN_WITH_COORDS.name}`,
        })
      ).toBeInTheDocument();
    });

    it('does not show the info strip when selectedOnsen is null', () => {
      render(<MapPanel state={makeState({ selectedOnsen: null })} dispatch={dispatch} />);
      expect(screen.queryByRole('region')).not.toBeInTheDocument();
    });

    it('dispatches HOVER_ONSEN null when the close button is clicked', async () => {
      const user = userEvent.setup();
      render(
        <MapPanel
          state={makeState({ selectedOnsen: ONSEN_WITH_COORDS })}
          dispatch={dispatch}
        />
      );

      await user.click(screen.getByRole('button', { name: /close onsen info strip/i }));
      expect(dispatch).toHaveBeenCalledWith({ type: 'HOVER_ONSEN', payload: null });
    });
  });

  // -------------------------------------------------------------------------
  // ResultsSummaryBar integration
  // -------------------------------------------------------------------------

  describe('ResultsSummaryBar integration', () => {
    it('shows the reset button and onsen count when onsens are present', () => {
      render(
        <MapPanel
          state={makeState({ onsens: [ONSEN_WITH_COORDS] })}
          dispatch={dispatch}
        />
      );
      expect(screen.getByRole('button', { name: /reset search results/i })).toBeInTheDocument();
      expect(screen.getByText('1')).toBeInTheDocument();
    });

    it('dispatches RESET when the reset button is clicked', async () => {
      const user = userEvent.setup();
      render(
        <MapPanel
          state={makeState({ onsens: [ONSEN_WITH_COORDS] })}
          dispatch={dispatch}
        />
      );

      await user.click(screen.getByRole('button', { name: /reset search results/i }));
      expect(dispatch).toHaveBeenCalledWith({ type: 'RESET' });
    });
  });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import HotelPanel from './HotelPanel';
import { initialState } from '../../reducer/appReducer';

// HotelPanel renders its real children (HotelList, HotelCardSkeleton,
// HotelPanelEmpty) — these tests exercise the wrapper's state branching
// against those real components.

function makeState(overrides = {}) {
  return { ...initialState, ...overrides };
}

function makeHotel(overrides = {}) {
  return {
    name: 'Coral Beach Hotel',
    location: 'Naha',
    price: 18000,
    url: 'https://travel.rakuten.com/coral',
    lat: 26.21,
    lng: 127.68,
    ...overrides,
  };
}

describe('HotelPanel', () => {
  let dispatch;

  beforeEach(() => {
    dispatch = vi.fn();
  });

  it('always renders the panel header', () => {
    render(<HotelPanel state={makeState()} dispatch={dispatch} />);
    expect(screen.getByText('Nearby Hotels')).toBeInTheDocument();
    expect(screen.getByLabelText('Hotels panel')).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Empty state
  // -------------------------------------------------------------------------

  it('renders the empty state when there are no hotels', () => {
    render(<HotelPanel state={makeState({ hotels: [] })} dispatch={dispatch} />);
    // No "found" count and no loading/error UI.
    expect(screen.queryByText(/found/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Loading hotels')).not.toBeInTheDocument();
    expect(screen.queryByText('Could not load hotels')).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------------

  it('renders skeletons when loading hotels for a selected onsen', () => {
    const state = makeState({
      hotels: [],
      status: 'loading',
      activeMarkers: 'onsens',
      selectedOnsen: { name: 'Yamada Onsen', lat: 26.2, lng: 127.6 },
    });
    render(<HotelPanel state={state} dispatch={dispatch} />);
    expect(screen.getByLabelText('Loading hotels')).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Error state
  // -------------------------------------------------------------------------

  it('renders the error state when the hotel search failed', () => {
    const state = makeState({
      hotels: [],
      status: 'error',
      activeMarkers: 'both',
    });
    render(<HotelPanel state={state} dispatch={dispatch} />);
    expect(screen.getByText('Could not load hotels')).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Results state
  // -------------------------------------------------------------------------

  it('renders the hotel list and the found count when hotels are present', () => {
    const hotels = [makeHotel({ name: 'Hotel A' }), makeHotel({ name: 'Hotel B' })];
    render(<HotelPanel state={makeState({ hotels })} dispatch={dispatch} />);

    expect(screen.getByText('2 found')).toBeInTheDocument();
    expect(screen.getByText('Hotel A')).toBeInTheDocument();
    expect(screen.getByText('Hotel B')).toBeInTheDocument();
  });

  it('shows the "Near {onsen}" subtitle when an onsen is selected', () => {
    const state = makeState({
      hotels: [makeHotel()],
      selectedOnsen: { name: 'Yamada Onsen' },
    });
    render(<HotelPanel state={state} dispatch={dispatch} />);
    expect(screen.getByText('Yamada Onsen')).toBeInTheDocument();
  });

  it('dispatches SELECT_HOTEL when a hotel card is clicked', async () => {
    const user = userEvent.setup();
    const hotel = makeHotel({ name: 'Hotel A' });
    render(<HotelPanel state={makeState({ hotels: [hotel] })} dispatch={dispatch} />);

    await user.click(screen.getByLabelText('Hotel: Hotel A'));
    expect(dispatch).toHaveBeenCalledWith({ type: 'SELECT_HOTEL', payload: hotel });
  });
});

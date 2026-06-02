import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import HotelCard from './HotelCard';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a hotel object, overriding only the fields a test cares about. */
function makeHotel(overrides = {}) {
  return {
    name: 'Coral Beach Hotel',
    originalName: 'コーラルビーチホテル',
    location: 'Naha, Okinawa',
    hotelSpecial: 'Ocean-view rooms with private onsen.',
    price: 18000,
    image: 'https://example.com/coral.jpg',
    url: 'https://travel.rakuten.com/coral',
    lat: 26.21,
    lng: 127.68,
    distance: 0.4,
    ...overrides,
  };
}

describe('HotelCard', () => {
  let onSelect;

  beforeEach(() => {
    onSelect = vi.fn();
  });

  // -------------------------------------------------------------------------
  // Guard clause
  // -------------------------------------------------------------------------

  it('renders nothing when no hotel is provided', () => {
    const { container } = render(<HotelCard hotel={null} onSelect={onSelect} />);
    expect(container).toBeEmptyDOMElement();
  });

  // -------------------------------------------------------------------------
  // Core content
  // -------------------------------------------------------------------------

  it('renders the hotel name and original Japanese name', () => {
    render(<HotelCard hotel={makeHotel()} onSelect={onSelect} />);
    expect(screen.getByText('Coral Beach Hotel')).toBeInTheDocument();
    expect(screen.getByText('コーラルビーチホテル')).toBeInTheDocument();
  });

  it('does NOT render the original name when it equals the display name', () => {
    render(
      <HotelCard
        hotel={makeHotel({ name: 'Same Name', originalName: 'Same Name' })}
        onSelect={onSelect}
      />
    );
    // Only one node with that text (the heading), not a duplicate sub-line.
    expect(screen.getAllByText('Same Name')).toHaveLength(1);
  });

  it('renders the location and special feature', () => {
    render(<HotelCard hotel={makeHotel()} onSelect={onSelect} />);
    expect(screen.getByText('Naha, Okinawa')).toBeInTheDocument();
    expect(
      screen.getByText('Ocean-view rooms with private onsen.')
    ).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Image vs placeholder
  // -------------------------------------------------------------------------

  it('renders the hotel image when present', () => {
    render(<HotelCard hotel={makeHotel()} onSelect={onSelect} />);
    const img = screen.getByAltText('Photo of Coral Beach Hotel');
    expect(img).toHaveAttribute('src', 'https://example.com/coral.jpg');
  });

  it('renders a placeholder when there is no image', () => {
    render(<HotelCard hotel={makeHotel({ image: null })} onSelect={onSelect} />);
    expect(screen.queryByAltText(/Photo of/)).not.toBeInTheDocument();
    expect(screen.getByText('🏨')).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Distance + price formatting
  // -------------------------------------------------------------------------

  it('formats sub-kilometre distances in metres', () => {
    render(<HotelCard hotel={makeHotel({ distance: 0.4 })} onSelect={onSelect} />);
    expect(screen.getByText('400m away')).toBeInTheDocument();
  });

  it('formats distances of 1km or more in kilometres', () => {
    render(<HotelCard hotel={makeHotel({ distance: 2.345 })} onSelect={onSelect} />);
    expect(screen.getByText('2.3km away')).toBeInTheDocument();
  });

  it('formats the price with a yen sign and thousands separators', () => {
    render(<HotelCard hotel={makeHotel({ price: 18000 })} onSelect={onSelect} />);
    expect(screen.getByText('¥18,000/night')).toBeInTheDocument();
  });

  it('omits price and distance when they are absent', () => {
    render(
      <HotelCard
        hotel={makeHotel({ price: null, distance: null })}
        onSelect={onSelect}
      />
    );
    expect(screen.queryByText(/\/night/)).not.toBeInTheDocument();
    expect(screen.queryByText(/away/)).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Selection styling
  // -------------------------------------------------------------------------

  it('exposes the hotel name via aria-label on the card', () => {
    render(<HotelCard hotel={makeHotel()} isSelected onSelect={onSelect} />);
    expect(
      screen.getByLabelText('Hotel: Coral Beach Hotel')
    ).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Interaction
  // -------------------------------------------------------------------------

  it('calls onSelect with the hotel when the card is clicked', async () => {
    const user = userEvent.setup();
    const hotel = makeHotel();
    render(<HotelCard hotel={hotel} onSelect={onSelect} />);

    await user.click(screen.getByLabelText('Hotel: Coral Beach Hotel'));
    expect(onSelect).toHaveBeenCalledWith(hotel);
  });

  it('renders a booking link and does NOT trigger onSelect when it is clicked', async () => {
    const user = userEvent.setup();
    render(<HotelCard hotel={makeHotel()} onSelect={onSelect} />);

    const link = screen.getByRole('link', {
      name: /Book Coral Beach Hotel on Rakuten Travel/i,
    });
    expect(link).toHaveAttribute('href', 'https://travel.rakuten.com/coral');
    expect(link).toHaveAttribute('target', '_blank');

    await user.click(link);
    // stopPropagation means the card's onSelect must not fire from the link.
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('renders a "Show on map" button instead of a link when there is no url', async () => {
    const user = userEvent.setup();
    const hotel = makeHotel({ url: null });
    render(<HotelCard hotel={hotel} onSelect={onSelect} />);

    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    const button = screen.getByRole('button', {
      name: /Select Coral Beach Hotel on map/i,
    });
    await user.click(button);
    expect(onSelect).toHaveBeenCalledWith(hotel);
  });
});

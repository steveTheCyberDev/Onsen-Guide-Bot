import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import OnsenMiniCard from './OnsenMiniCard';

function makeOnsen(overrides = {}) {
  return {
    name: 'Yamada Onsen',
    location: 'Naha, Okinawa',
    spring_type: 'Sodium chloride',
    spa_quality: 'High mineral content',
    sales_point: 'Open-air bath overlooking the sea.',
    lat: 26.2,
    lng: 127.6,
    ...overrides,
  };
}

describe('OnsenMiniCard', () => {
  it('renders nothing when no onsen is provided', () => {
    const { container } = render(<OnsenMiniCard onsen={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the onsen name', () => {
    render(<OnsenMiniCard onsen={makeOnsen()} />);
    expect(screen.getByText('Yamada Onsen')).toBeInTheDocument();
  });

  it('renders location, spring type, quality and sales point when present', () => {
    render(<OnsenMiniCard onsen={makeOnsen()} />);
    expect(screen.getByText('Naha, Okinawa')).toBeInTheDocument();
    expect(screen.getByText('Sodium chloride')).toBeInTheDocument();
    expect(screen.getByText('High mineral content')).toBeInTheDocument();
    expect(
      screen.getByText('Open-air bath overlooking the sea.')
    ).toBeInTheDocument();
  });

  it('renders the Type and Quality labels', () => {
    render(<OnsenMiniCard onsen={makeOnsen()} />);
    expect(screen.getByText('Type:')).toBeInTheDocument();
    expect(screen.getByText('Quality:')).toBeInTheDocument();
  });

  it('omits optional fields that are absent', () => {
    render(
      <OnsenMiniCard
        onsen={makeOnsen({
          location: null,
          spring_type: null,
          spa_quality: null,
          sales_point: null,
        })}
      />
    );
    expect(screen.getByText('Yamada Onsen')).toBeInTheDocument();
    expect(screen.queryByText('Type:')).not.toBeInTheDocument();
    expect(screen.queryByText('Quality:')).not.toBeInTheDocument();
    expect(screen.queryByText('Naha, Okinawa')).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Interactive (onSelect provided) — renders as a button
  // -------------------------------------------------------------------------

  describe('interactive mode (onSelect provided)', () => {
    it('renders as a button when onSelect is provided', () => {
      render(<OnsenMiniCard onsen={makeOnsen()} onSelect={vi.fn()} />);
      expect(
        screen.getByRole('button', { name: /show yamada onsen on map/i })
      ).toBeInTheDocument();
    });

    it('calls onSelect with the onsen object when clicked', async () => {
      const onsen = makeOnsen();
      const onSelect = vi.fn();
      render(<OnsenMiniCard onsen={onsen} onSelect={onSelect} />);

      await userEvent.click(screen.getByRole('button', { name: /show yamada onsen on map/i }));

      expect(onSelect).toHaveBeenCalledOnce();
      expect(onSelect).toHaveBeenCalledWith(onsen);
    });

    it('is keyboard-accessible: activating with Enter calls onSelect', async () => {
      const onsen = makeOnsen();
      const onSelect = vi.fn();
      render(<OnsenMiniCard onsen={onsen} onSelect={onSelect} />);

      const button = screen.getByRole('button', { name: /show yamada onsen on map/i });
      button.focus();
      await userEvent.keyboard('{Enter}');

      expect(onSelect).toHaveBeenCalledOnce();
      expect(onSelect).toHaveBeenCalledWith(onsen);
    });

    it('is keyboard-accessible: activating with Space calls onSelect', async () => {
      const onsen = makeOnsen();
      const onSelect = vi.fn();
      render(<OnsenMiniCard onsen={onsen} onSelect={onSelect} />);

      const button = screen.getByRole('button', { name: /show yamada onsen on map/i });
      button.focus();
      await userEvent.keyboard(' ');

      expect(onSelect).toHaveBeenCalledOnce();
    });

    it('does NOT call onSelect when the onsen has no coordinates (no-op guard)', async () => {
      const onSelect = vi.fn();
      // lat/lng omitted — hasCoords is false inside the component
      render(
        <OnsenMiniCard
          onsen={makeOnsen({ lat: undefined, lng: undefined })}
          onSelect={onSelect}
        />
      );

      await userEvent.click(screen.getByRole('button', { name: /show yamada onsen on map/i }));

      expect(onSelect).not.toHaveBeenCalled();
    });

    it('still renders the card content when onsen has no coords', () => {
      render(
        <OnsenMiniCard
          onsen={makeOnsen({ lat: undefined, lng: undefined })}
          onSelect={vi.fn()}
        />
      );
      expect(screen.getByText('Yamada Onsen')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Non-interactive mode (no onSelect) — renders as a plain div
  // -------------------------------------------------------------------------

  describe('non-interactive mode (no onSelect)', () => {
    it('does NOT render a button when onSelect is absent', () => {
      render(<OnsenMiniCard onsen={makeOnsen()} />);
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('still renders the onsen name in non-interactive mode', () => {
      render(<OnsenMiniCard onsen={makeOnsen()} />);
      expect(screen.getByText('Yamada Onsen')).toBeInTheDocument();
    });
  });
});

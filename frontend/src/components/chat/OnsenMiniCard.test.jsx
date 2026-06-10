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

  it('renders location, spring type, and spa_quality description when present', () => {
    render(<OnsenMiniCard onsen={makeOnsen()} />);
    expect(screen.getByText('Naha, Okinawa')).toBeInTheDocument();
    expect(screen.getByText('Sodium chloride')).toBeInTheDocument();
    expect(screen.getByText('High mineral content')).toBeInTheDocument();
  });

  it('renders the Type label but no Quality label; spa_quality text renders as a plain paragraph', () => {
    render(<OnsenMiniCard onsen={makeOnsen()} />);
    expect(screen.getByText('Type:')).toBeInTheDocument();
    expect(screen.queryByText('Quality:')).not.toBeInTheDocument();
    expect(screen.getByText('High mineral content')).toBeInTheDocument();
  });

  it('omits optional fields that are absent', () => {
    render(
      <OnsenMiniCard
        onsen={makeOnsen({
          location: null,
          spring_type: null,
          spa_quality: null,
        })}
      />
    );
    expect(screen.getByText('Yamada Onsen')).toBeInTheDocument();
    expect(screen.queryByText('Type:')).not.toBeInTheDocument();
    expect(screen.queryByText('Quality:')).not.toBeInTheDocument();
    expect(screen.queryByText('Naha, Okinawa')).not.toBeInTheDocument();
    expect(screen.queryByText('High mineral content')).not.toBeInTheDocument();
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

  // -------------------------------------------------------------------------
  // pros / cons block (recommend mode + analyze enabled)
  // -------------------------------------------------------------------------

  describe('pros/cons block', () => {
    it('renders the pros/cons block with correct items and symbols when both present', () => {
      const onsen = makeOnsen({
        pros: ['Great view', 'Quiet location'],
        cons: ['Far from station'],
      });
      render(<OnsenMiniCard onsen={onsen} />);

      const block = screen.getByTestId('onsen-pros-cons');
      expect(block).toBeInTheDocument();

      const prosList = screen.getByRole('list', { name: 'Yamada Onsen pros' });
      expect(prosList).toBeInTheDocument();
      expect(screen.getByText('Great view')).toBeInTheDocument();
      expect(screen.getByText('Quiet location')).toBeInTheDocument();

      const consList = screen.getByRole('list', { name: 'Yamada Onsen cons' });
      expect(consList).toBeInTheDocument();
      expect(screen.getByText('Far from station')).toBeInTheDocument();

      // ✓ symbols for pros, ✕ symbols for cons
      const checks = prosList.querySelectorAll('span[aria-hidden="true"]');
      checks.forEach((el) => expect(el.textContent).toBe('✓'));

      const crosses = consList.querySelectorAll('span[aria-hidden="true"]');
      crosses.forEach((el) => expect(el.textContent).toBe('✕'));
    });

    it('renders only the pros list when cons is empty', () => {
      const onsen = makeOnsen({ pros: ['Great view'], cons: [] });
      render(<OnsenMiniCard onsen={onsen} />);

      expect(screen.getByTestId('onsen-pros-cons')).toBeInTheDocument();
      expect(screen.getByRole('list', { name: 'Yamada Onsen pros' })).toBeInTheDocument();
      expect(screen.queryByRole('list', { name: 'Yamada Onsen cons' })).not.toBeInTheDocument();
      expect(screen.getByText('Great view')).toBeInTheDocument();
    });

    it('renders only the cons list when pros is empty', () => {
      const onsen = makeOnsen({ pros: [], cons: ['Far from station'] });
      render(<OnsenMiniCard onsen={onsen} />);

      expect(screen.getByTestId('onsen-pros-cons')).toBeInTheDocument();
      expect(screen.queryByRole('list', { name: 'Yamada Onsen pros' })).not.toBeInTheDocument();
      expect(screen.getByRole('list', { name: 'Yamada Onsen cons' })).toBeInTheDocument();
      expect(screen.getByText('Far from station')).toBeInTheDocument();
    });

    it('renders no pros/cons block when both pros and cons are empty', () => {
      const onsen = makeOnsen({ pros: [], cons: [] });
      render(<OnsenMiniCard onsen={onsen} />);
      expect(screen.queryByTestId('onsen-pros-cons')).not.toBeInTheDocument();
    });

    it('renders no pros/cons block when pros and cons are absent (search-mode card unchanged)', () => {
      const onsen = makeOnsen();
      delete onsen.pros;
      delete onsen.cons;
      render(<OnsenMiniCard onsen={onsen} />);
      expect(screen.queryByTestId('onsen-pros-cons')).not.toBeInTheDocument();
    });
  });
});

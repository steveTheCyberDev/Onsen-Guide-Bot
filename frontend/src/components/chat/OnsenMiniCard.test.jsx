import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import OnsenMiniCard from './OnsenMiniCard';

function makeOnsen(overrides = {}) {
  return {
    name: 'Yamada Onsen',
    location: 'Naha, Okinawa',
    spring_type: 'Sodium chloride',
    spa_quality: 'High mineral content',
    sales_point: 'Open-air bath overlooking the sea.',
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
});

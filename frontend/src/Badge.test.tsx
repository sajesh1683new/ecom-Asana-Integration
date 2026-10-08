import {render,screen} from '@testing-library/react';
import {describe,it,expect} from 'vitest';
import {Badge} from './Badge';

describe('Human decision guidance',()=>{
  it('keeps clarification visibly distinct from a confirmed defect',()=>{
    render(<Badge status="Blocked"/>);
    expect(screen.getByText('Blocked')).toHaveAttribute('title',expect.stringContaining('missing element'));
  });
  it('makes release ownership explicit',()=>{
    render(<Badge status="Approved for release"/>);
    expect(screen.getByText('Approved for release')).toHaveAttribute('title','Release owner explicitly approved the change.');
  });
});

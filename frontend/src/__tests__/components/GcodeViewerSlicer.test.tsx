/**
 * Fork (printfarm): GcodeViewer's slicer layout. The move slider cuts the top layer's commands; a new file in
 * the same viewer must not get the cut of the previous one back when the layer slider moves.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

const { previews, FakePreview } = vi.hoisted(() => {
  const previews: { layers: { commands: string[] }[] }[] = [];
  class FakePreview {
    layers: { commands: string[] }[] = [];
    endLayer?: number;
    extrusionColor: unknown;
    constructor() {
      previews.push(this);
    }
    processGCode(gcode: string) {
      this.layers = gcode
        .split(';LAYER_CHANGE')
        .slice(1)
        .map((block) => ({ commands: block.split('\n').filter((l) => l.startsWith('G1')) }));
    }
    render() {}
    resize() {}
    dispose() {}
  }
  return { previews, FakePreview };
});

vi.mock('gcode-preview', () => ({ WebGLPreview: FakePreview }));

import { GcodeViewer } from '../../components/GcodeViewer';

const FILES: Record<string, string> = {
  '/a.gcode': ';LAYER_CHANGE\nG1 X1 E1\nG1 X2 E2\n;LAYER_CHANGE\nG1 X3 E3\nG1 X4 E4\nG1 X5 E5\n',
  '/b.gcode': ';LAYER_CHANGE\nG1 Y1 E1\n;LAYER_CHANGE\nG1 Y2 E2\nG1 Y3 E3\n',
};

beforeEach(() => {
  previews.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => new Response(FILES[url], { status: 200 })),
  );
});

describe('GcodeViewer slicer layout', () => {
  it('a new file starts without the move cut of the previous one', async () => {
    const { rerender } = render(<GcodeViewer gcodeUrl="/a.gcode" controls="slicer" />);
    await waitFor(() => expect(screen.getByRole('slider', { name: 'move' })).toBeInTheDocument());
    act(() => {
      fireEvent.change(screen.getByRole('slider', { name: 'move' }), { target: { value: '1' } });
    });
    expect(previews[0].layers[1].commands).toEqual(['G1 X3 E3']); // the top layer of a, cut at the move

    rerender(<GcodeViewer gcodeUrl="/b.gcode" controls="slicer" />);
    await waitFor(() => expect(previews).toHaveLength(2));
    await waitFor(() => expect(previews[1].layers).toHaveLength(2));
    await waitFor(() => expect(screen.getByRole('slider', { name: 'move' })).toHaveAttribute('max', '2'));
    act(() => {
      fireEvent.change(screen.getByRole('slider', { name: 'layer' }), { target: { value: '1' } });
    });
    expect(previews[1].layers[1].commands).toEqual(['G1 Y2 E2', 'G1 Y3 E3']); // b's own, not a's
  });
});

import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { WebGLPreview } from 'gcode-preview';
import { Loader2, Layers, ChevronLeft, ChevronRight, FileWarning } from 'lucide-react';
import { getAuthToken } from '../api/client';
import { FEATURE_COLORS, FEATURE_GROUPS, colorByFeature } from '../utils/gcodeFeatures';

interface GcodeViewerProps {
  gcodeUrl: string;
  buildVolume?: { x: number; y: number; z: number };
  filamentColors?: string[];
  className?: string;
  /** Fork: colour by filament (default) or by feature type, as a slicer's preview. */
  colorBy?: 'tool' | 'feature';
  /** Fork: a layer bar below (default), or the slicer layout: vertical layer slider, move slider, legend. */
  controls?: 'bar' | 'slicer';
  /** Fork: the legend's names of the feature groups (``FEATURE_GROUPS`` order), translated by the caller. */
  featureLabels?: string[];
}

export function GcodeViewer({
  gcodeUrl,
  buildVolume = { x: 256, y: 256, z: 256 },
  filamentColors,
  className = '',
  colorBy = 'tool',
  controls = 'bar',
  featureLabels,
}: GcodeViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const previewRef = useRef<WebGLPreview | null>(null);
  const renderTimeoutRef = useRef<number | null>(null);
  const initRef = useRef(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notSliced, setNotSliced] = useState(false);
  const [currentLayer, setCurrentLayer] = useState(0);
  const [totalLayers, setTotalLayers] = useState(0);
  // Fork: slicer layout. Feature groups in the file, and the move slider over the top layer's commands.
  const [groups, setGroups] = useState<number[]>([]);
  const [moveMax, setMoveMax] = useState(0);
  const [move, setMove] = useState(0);
  const truncatedRef = useRef<{ index: number; commands: unknown[] } | null>(null); // the top layer, cut at the move

  // Memoize colors to prevent re-renders
  const colorsKey = useMemo(() => JSON.stringify(filamentColors), [filamentColors]);

  useEffect(() => {
    if (!canvasRef.current || initRef.current) return;
    initRef.current = true;

    const canvas = canvasRef.current;

    // Set canvas size before creating preview
    const rect = canvas.parentElement?.getBoundingClientRect();
    if (rect) {
      canvas.width = rect.width;
      canvas.height = rect.height;
    }

    // Use extrusionColor as array for multi-tool support
    // Index in array = tool number
    const hasMultiColor = filamentColors && filamentColors.length > 1;
    const primaryColor = filamentColors?.[0] || '#00ae42';

    // Create preview
    const preview = new WebGLPreview({
      canvas,
      buildVolume,
      backgroundColor: 0x1a1a1a,
      // Pass full color array - library uses index as tool number
      extrusionColor: hasMultiColor ? filamentColors : primaryColor,
      disableGradient: true,
      lineHeight: 0.2,
      lineWidth: 2,
      renderTravel: false,
      renderExtrusion: true,
    });

    previewRef.current = preview;

    // Fetch and process gcode
    const headers: HeadersInit = {};
    const token = getAuthToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    fetch(gcodeUrl, { headers })
      .then(async response => {
        if (!response.ok) {
          if (response.status === 404) {
            const data = await response.json().catch(() => ({}));
            if (data.detail?.includes('sliced')) {
              setNotSliced(true);
              throw new Error('not_sliced');
            }
          }
          throw new Error('Failed to load G-code');
        }
        return response.text();
      })
      .then(gcode => {
        if (colorBy === 'feature') {
          const byFeature = colorByFeature(gcode);
          setGroups(byFeature.groups);
          (preview as unknown as { extrusionColor: string[] }).extrusionColor = FEATURE_COLORS;
          return byFeature.gcode;
        }
        // The gcode-preview library only supports T0-T7
        // We need to remap higher tool numbers to fit within this range
        // First, find all unique tool numbers used
        const toolNumbers = new Set<number>();
        const toolRegex = /^(\s*)T(\d+)(\s*;.*)?$/gim;
        let match;
        while ((match = toolRegex.exec(gcode)) !== null) {
          const toolNum = parseInt(match[2], 10);
          if (toolNum <= 15) { // Valid tool, not a special command
            toolNumbers.add(toolNum);
          }
        }

        // Create a mapping from original tool numbers to 0-7 range
        const toolMapping = new Map<number, number>();
        const sortedTools = Array.from(toolNumbers).sort((a, b) => a - b);
        sortedTools.forEach((tool, index) => {
          toolMapping.set(tool, index % 8); // Map to 0-7
        });

        // Build remapped color array based on the mapping
        const remappedColors: string[] = [];
        sortedTools.forEach((originalTool, index) => {
          const color = filamentColors?.[originalTool] || '#00ae42';
          remappedColors[index % 8] = color;
        });

        // Process gcode: filter special commands and remap tool numbers
        const cleanedGcode = gcode
          .split('\n')
          .map(line => {
            const match = line.match(/^(\s*)T(\d+)(\s*;.*)?$/i);
            if (match) {
              const toolNum = parseInt(match[2], 10);
              if (toolNum > 15) {
                // Filter out Bambu special commands (T255, T1000, T65535, etc.)
                return `; FILTERED: ${line.trim()}`;
              }
              // Remap tool number to 0-7 range
              const mappedTool = toolMapping.get(toolNum) ?? 0;
              return `${match[1]}T${mappedTool}${match[3] || ''}`;
            }
            return line;
          })
          .join('\n');

        // Update colors for the preview using the remapped array
        if (remappedColors.length > 0) {
          (preview as unknown as { extrusionColor: string[] }).extrusionColor = remappedColors;
        }
        return cleanedGcode;
      })
      .then(processed => {
        preview.processGCode(processed);

        const layers = preview.layers?.length || 0;
        setTotalLayers(layers);
        setCurrentLayer(layers);
        const topMoves = layers ? preview.layers[layers - 1].commands.length : 0;
        setMoveMax(topMoves);
        setMove(topMoves);

        preview.render();
        setLoading(false);
      })
      .catch(err => {
        if (err.message !== 'not_sliced') {
          setError(err.message);
        }
        setLoading(false);
      });

    // Handle resize
    const handleResize = () => {
      if (canvas.parentElement && previewRef.current) {
        const newRect = canvas.parentElement.getBoundingClientRect();
        canvas.width = newRect.width;
        canvas.height = newRect.height;
        previewRef.current.resize();
      }
    };

    window.addEventListener('resize', handleResize);
    // Fork: also when the container itself changes size (shown after being hidden, a panel resized)
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(handleResize) : null;
    if (canvas.parentElement) observer?.observe(canvas.parentElement);

    return () => {
      window.removeEventListener('resize', handleResize);
      observer?.disconnect();
      if (renderTimeoutRef.current) {
        cancelAnimationFrame(renderTimeoutRef.current);
      }
      if (previewRef.current) {
        previewRef.current.dispose();
        previewRef.current = null;
      }
      initRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gcodeUrl, colorsKey, colorBy]); // Intentionally use colorsKey instead of filamentColors, buildVolume rarely changes

  const handleLayerChange = useCallback((layer: number) => {
    if (!previewRef.current) return;
    const newLayer = Math.max(1, Math.min(layer, totalLayers));
    setCurrentLayer(newLayer);
    const cut = truncatedRef.current; // a new top layer: the previous one is shown whole again
    if (cut) {
      (previewRef.current.layers[cut.index] as unknown as { commands: unknown[] }).commands = cut.commands;
      truncatedRef.current = null;
    }
    const moves = previewRef.current.layers[newLayer - 1]?.commands.length ?? 0;
    setMoveMax(moves);
    setMove(moves);

    if (renderTimeoutRef.current) {
      cancelAnimationFrame(renderTimeoutRef.current);
    }

    renderTimeoutRef.current = requestAnimationFrame(() => {
      if (previewRef.current) {
        previewRef.current.endLayer = newLayer;
        previewRef.current.render();
      }
    });
  }, [totalLayers]);

  /** Fork: draw the top layer only up to its ``count``-th command (the slicer's move slider). */
  const handleMoveChange = useCallback((count: number) => {
    const preview = previewRef.current;
    const layer = preview?.layers[currentLayer - 1] as unknown as { commands: unknown[] } | undefined;
    if (!preview || !layer) return;
    if (!truncatedRef.current) truncatedRef.current = { index: currentLayer - 1, commands: layer.commands };
    layer.commands = truncatedRef.current.commands.slice(0, count);
    setMove(count);
    if (renderTimeoutRef.current) cancelAnimationFrame(renderTimeoutRef.current);
    renderTimeoutRef.current = requestAnimationFrame(() => previewRef.current?.render());
  }, [currentLayer]);

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    handleLayerChange(parseInt(e.target.value, 10));
  };

  return (
    <div className={`relative flex flex-col h-full ${className}`}>
      <div className="flex-1 relative bg-bambu-dark rounded-lg overflow-hidden">
        <canvas ref={canvasRef} className="w-full h-full" />

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-bambu-dark/80">
            <div className="text-center">
              <Loader2 className="w-8 h-8 animate-spin text-bambu-green mx-auto mb-2" />
              <p className="text-bambu-gray text-sm">Loading G-code...</p>
            </div>
          </div>
        )}

        {notSliced && (
          <div className="absolute inset-0 flex items-center justify-center bg-bambu-dark/80">
            <div className="text-center max-w-sm px-4">
              <FileWarning className="w-12 h-12 text-bambu-gray mx-auto mb-3" />
              <p className="text-white font-medium mb-2">G-code not available</p>
              <p className="text-bambu-gray text-sm">
                This file hasn't been sliced yet. G-code preview is only available
                after slicing in Bambu Studio or Orca Slicer.
              </p>
            </div>
          </div>
        )}

        {error && !notSliced && (
          <div className="absolute inset-0 flex items-center justify-center bg-bambu-dark/80">
            <div className="text-center text-red-400">
              <p className="text-sm">{error}</p>
            </div>
          </div>
        )}

        {controls === 'slicer' && !loading && !error && !notSliced && totalLayers > 0 && (
          <>
            {colorBy === 'feature' && groups.length > 0 && (
              <ul className="absolute top-2 left-2 space-y-1 rounded-md bg-bambu-dark-secondary/90 p-2 text-xs text-white" aria-label="legend">
                {groups.map((g) => (
                  <li key={g} className="flex items-center gap-2">
                    <span className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: FEATURE_COLORS[g] }} aria-hidden />
                    {featureLabels?.[g] ?? FEATURE_GROUPS[g]}
                  </li>
                ))}
              </ul>
            )}
            <div className="absolute right-2 top-2 bottom-12 flex flex-col items-center gap-1 text-xs text-bambu-gray-light">
              <span>{totalLayers}</span>
              <input
                type="range"
                min={1}
                max={totalLayers}
                value={currentLayer}
                onChange={handleSliderChange}
                aria-label="layer"
                className="flex-1 w-5 accent-bambu-green cursor-pointer"
                style={{ writingMode: 'vertical-lr', direction: 'rtl' }}
              />
              <span className="rounded bg-bambu-dark-secondary px-1.5 py-0.5 text-white">{currentLayer}</span>
            </div>
            <div className="absolute left-2 right-12 bottom-2 flex items-center gap-2 text-xs text-bambu-gray-light">
              <input
                type="range"
                min={0}
                max={moveMax}
                value={move}
                onChange={(e) => handleMoveChange(parseInt(e.target.value, 10))}
                aria-label="move"
                className="flex-1 accent-bambu-green cursor-pointer"
              />
              <span className="min-w-[72px] text-right">
                {move} / {moveMax}
              </span>
            </div>
          </>
        )}
      </div>

      {controls === 'bar' && !loading && !error && !notSliced && totalLayers > 0 && (
        <div className="mt-4 px-2">
          <div className="flex items-center gap-3">
            <Layers className="w-4 h-4 text-bambu-gray flex-shrink-0" />

            <button
              onClick={() => handleLayerChange(currentLayer - 1)}
              disabled={currentLayer <= 1}
              className="p-1 rounded hover:bg-bambu-dark-tertiary disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>

            <input
              type="range"
              min={1}
              max={totalLayers}
              value={currentLayer}
              onChange={handleSliderChange}
              className="flex-1 h-2 bg-bambu-dark-tertiary rounded-lg appearance-none cursor-pointer accent-bambu-green"
            />

            <button
              onClick={() => handleLayerChange(currentLayer + 1)}
              disabled={currentLayer >= totalLayers}
              className="p-1 rounded hover:bg-bambu-dark-tertiary disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ChevronRight className="w-4 h-4" />
            </button>

            <span className="text-sm text-bambu-gray min-w-[80px] text-right">
              {currentLayer} / {totalLayers}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

// Fork (printfarm): OrcaSlicer's "Arrange options" (GLCanvas3D::_render_arrange_menu, v2.5.0), opened by a right
// click on the toolbar's Arrange as in Orca. Arrange itself runs in Orca's own command line in the slicer sidecar
// (backend farm/workspace/arrange.py), which arranges with automatic spacing: Spacing shows 0 ("0 means auto
// spacing.") and can't be changed. The options are kept per browser, as Orca keeps them in its app config.
import { useTranslation } from 'react-i18next';
import { Button } from '../../components/Button';
import { Dialog } from './Dialogs';
import { type ArrangeSettings, arrangeDefaults } from './arrange';
import { type OrcaLayout, orcaText } from './orcaLayout';
import '../i18n';

export function ArrangeOptionsDialog({
  layout,
  value,
  lidar,
  i3,
  onChange,
  onArrange,
  onClose,
}: {
  layout: OrcaLayout;
  value: ArrangeSettings;
  /** the printer scans its first layer (a Bambu lidar): "Avoid extrusion calibration region" shows */
  lidar: boolean;
  i3: boolean;
  onChange: (s: ArrangeSettings) => void;
  onArrange: () => void;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const box = (key: keyof ArrangeSettings, label: string, disabled = false) => (
    <label className={`flex items-center gap-2 text-[13px] ${disabled ? 'text-bambu-gray' : 'text-bambu-gray-light'}`}>
      <input
        type="checkbox"
        checked={value[key] && !disabled}
        disabled={disabled}
        onChange={(e) => onChange({ ...value, [key]: e.target.checked })}
        className="accent-bambu-green"
      />
      {tr(label)}
    </label>
  );
  return (
    <Dialog title={tr('Arrange options')} onClose={onClose}>
      <div className="flex flex-col gap-3 px-6 py-4">
        <div className="flex items-center gap-3">
          <span className="text-[13px] text-bambu-gray-light">{tr('Spacing')}</span>
          <input
            type="text"
            value="0.00"
            disabled
            aria-label={tr('Spacing')}
            title={t('farm.workspace.arrange.spacingAuto')}
            className="h-7 w-20 rounded border border-bambu-dark-tertiary bg-bambu-dark px-2 text-[13px] text-bambu-gray"
          />
        </div>
        <p className="text-xs text-bambu-gray">
          {tr('0 means auto spacing.')} {t('farm.workspace.arrange.spacingAuto')}
        </p>
        <hr className="border-bambu-dark-tertiary" />
        {box('rotation', 'Auto rotate for arrangement')}
        {box('multiMaterial', 'Allow multiple materials on same plate')}
        {lidar && box('avoidCalibration', 'Avoid extrusion calibration region')}
        {box('alignY', 'Align to Y axis', value.rotation)}
      </div>
      <div className="flex justify-end gap-2 border-t border-bambu-dark-tertiary p-3">
        <Button variant="secondary" onClick={() => onChange(arrangeDefaults(i3))}>
          {tr('Reset')}
        </Button>
        <Button
          onClick={() => {
            onArrange();
            onClose();
          }}
        >
          {tr('Arrange')}
        </Button>
      </div>
    </Dialog>
  );
}

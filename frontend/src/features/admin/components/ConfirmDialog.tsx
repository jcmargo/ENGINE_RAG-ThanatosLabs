export function ConfirmDialog({
  title,
  effect,
  confirmLabel,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string;
  effect: string;
  confirmLabel: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <dialog
      open
      aria-labelledby="confirm-title"
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
    >
      <h2 id="confirm-title">{title}</h2>
      <p>{effect}</p>
      <div className="dialog-actions">
        <button type="button" onClick={onConfirm} disabled={busy}>
          {confirmLabel}
        </button>
        <button type="button" className="secondary" onClick={onCancel} autoFocus>
          Cancelar
        </button>
      </div>
    </dialog>
  );
}

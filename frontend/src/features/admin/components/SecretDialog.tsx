import { useState } from "react";

export function SecretDialog({
  title,
  secretLabel,
  secret,
  onClose,
}: {
  title: string;
  secretLabel: string;
  secret: string;
  onClose: () => void;
}) {
  const [saved, setSaved] = useState(false);
  return (
    <dialog
      open
      aria-labelledby="secret-title"
      onCancel={(event) => {
        event.preventDefault();
      }}
    >
      <h2 id="secret-title">{title}</h2>
      <p>Este valor se muestra una sola vez. Cópialo antes de cerrar.</p>
      <p>
        <span>{secretLabel}</span> <code className="secret">{secret}</code>
      </p>
      <label>
        <input
          type="checkbox"
          checked={saved}
          onChange={(event) => {
            setSaved(event.target.checked);
          }}
        />
        Ya lo guardé
      </label>
      <div className="dialog-actions">
        <button type="button" disabled={!saved} onClick={onClose}>
          Cerrar
        </button>
      </div>
    </dialog>
  );
}

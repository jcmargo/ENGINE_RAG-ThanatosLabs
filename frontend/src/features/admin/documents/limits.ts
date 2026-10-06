export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md", ".markdown"] as const;

export function uploadProblem(file: File): string | null {
  const name = file.name.trim().toLowerCase();
  const allowed = ALLOWED_EXTENSIONS.some((extension) => name.endsWith(extension));
  if (!allowed) {
    return "El tipo de archivo no está permitido.";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return "El archivo supera el máximo de 25 MiB.";
  }
  return null;
}

export function isPendingStatus(status: string): boolean {
  return status === "queued" || status === "processing" || status === "deleting";
}

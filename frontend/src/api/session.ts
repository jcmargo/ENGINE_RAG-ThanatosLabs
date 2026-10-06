/** Access token solo en memoria. El refresh vive en la cookie HttpOnly. */

export type Session = {
  accessToken: string;
  expiresIn: number;
  principalType: string;
  userId: string | null;
  tenantId: string | null;
};

type Listener = (session: Session | null) => void;

let session: Session | null = null;
const listeners = new Set<Listener>();

export function getSession(): Session | null {
  return session;
}

export function setSession(next: Session | null): void {
  session = next;
  for (const listener of listeners) {
    listener(next);
  }
}

export function subscribeSession(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function resetSessionForTests(): void {
  session = null;
  listeners.clear();
}

const BASE_MS = 1000;
const CAP_MS = 8000;

let scale = 1;

export function setPollScaleForTests(next: number): void {
  scale = next;
}

export function resetPollScaleForTests(): void {
  scale = 1;
}

export function pollDelay(attempt: number): number {
  const raw = Math.min(BASE_MS * 2 ** attempt, CAP_MS);
  return Math.max(1, Math.round(raw * scale));
}

export type PollClock = {
  updatedAt: number;
  attempt: number;
};

export function nextPollDelay(pending: boolean, clock: PollClock, updatedAt: number): number | false {
  if (!pending) {
    clock.updatedAt = 0;
    clock.attempt = 0;
    return false;
  }
  if (clock.updatedAt !== updatedAt) {
    const delay = pollDelay(clock.attempt);
    clock.updatedAt = updatedAt;
    clock.attempt += 1;
    return delay;
  }
  return pollDelay(Math.max(0, clock.attempt - 1));
}

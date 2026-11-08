export interface BackoffOptions {
  baseMs: number;
  capMs: number;
}

export const DEFAULT_BACKOFF: BackoffOptions = { baseMs: 250, capMs: 8_000 };

/**
 * Exponential back-off with "full jitter" (delay drawn uniformly from [0, min(cap, base * 2^attempt)]).
 * Jitter matters even for a single local client: after the backend restarts, the UI, a dev tools tab and a browser
 * preview would otherwise all reconnect on the same tick.
 */
export function backoffDelay(
  attempt: number,
  options: BackoffOptions = DEFAULT_BACKOFF,
  random: () => number = Math.random,
): number {
  const ceiling = Math.min(options.capMs, options.baseMs * 2 ** Math.max(0, attempt));
  return Math.floor(random() * ceiling);
}

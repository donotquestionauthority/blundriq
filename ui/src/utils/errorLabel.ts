/**
 * What the browser console may say about a caught value: its class (`ApiError`, `TypeError`), or
 * its type when it is not an Error. Never the message, which can carry a URL, a response body or
 * a stack. `consoleLogging.test.ts` holds every console call to this.
 */
export function errorLabel(e: unknown): string {
  return e instanceof Error ? e.name : typeof e;
}

// Work email for Kulan Fleet: one set of mailbox functions for fleet-admin,
// whichever provider the organisation connected (Google Workspace, google.ts;
// Microsoft 365, microsoft.ts, 2.12). Both say what went wrong with the same
// error (MailError: an HTTP-like status and a plain sentence).
import * as google from "./google.ts";
import * as microsoft from "./microsoft.ts";

export const MailError = google.GoogleError;
export type MailError = google.GoogleError;
export type MailUser = google.GoogleUser;
export type Provider = "google" | "microsoft";
export type Connection =
  | ({ provider: "google" } & google.Connection)
  | ({ provider: "microsoft" } & microsoft.Connection);

export const ADDRESS = google.ADDRESS;
export const sealText = google.sealText;
export const openText = google.openText;
export const splitName = google.splitName;

// What people call it, in sentences.
export const label = (p: Provider | string | null | undefined) => p === "microsoft" ? "Microsoft 365" : "Google Workspace";
export const short = (p: Provider | string | null | undefined) => p === "microsoft" ? "Microsoft" : "Google";

export const getUser = (c: Connection, key: string): Promise<MailUser | null> =>
  c.provider === "microsoft" ? microsoft.getUser(c, key) : google.getUser(c, key);
export const listUsers = (c: Connection): Promise<MailUser[]> =>
  c.provider === "microsoft" ? microsoft.listUsers(c) : google.listUsers(c);
export const createUser = (c: Connection, address: string, name: { given: string; family: string }, password: string) =>
  c.provider === "microsoft" ? microsoft.createUser(c, address, name, password) : google.createUser(c, address, name, password);
export const setSuspended = (c: Connection, key: string, suspended: boolean) =>
  c.provider === "microsoft" ? microsoft.setSuspended(c, key, suspended) : google.setSuspended(c, key, suspended);
export const rename = (c: Connection, key: string, name: { given: string; family: string }) =>
  c.provider === "microsoft" ? microsoft.rename(c, key, name) : google.rename(c, key, name);
export const setPassword = (c: Connection, key: string, password: string) =>
  c.provider === "microsoft" ? microsoft.setPassword(c, key, password) : google.setPassword(c, key, password);
export const deleteUser = (c: Connection, key: string) =>
  c.provider === "microsoft" ? microsoft.deleteUser(c, key) : google.deleteUser(c, key);

// A first password, shown to the admin once (a new one is asked for at the
// first sign-in): 16 letters and digits that can't be mistaken for each
// other, in groups of four, always with a capital, a small letter and a digit
// (Microsoft 365 wants three kinds of character).
export function tempPassword(): string {
  for (;;) {
    const p = google.tempPassword();
    if (/[a-z]/.test(p) && /[A-Z]/.test(p) && /[0-9]/.test(p)) return p;
  }
}

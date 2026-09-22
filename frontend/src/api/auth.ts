import { apiClient } from "./client";

export interface CodeRequestResult {
  sent: boolean;
  /** "email" when a mailbox got it, "log" on an install with no SMTP. The
   *  sign-in screen tells the user where to look rather than leaving them at
   *  an empty inbox. */
  delivery: "email" | "log";
  message: string;
}

export interface SessionResult {
  token: string;
  email: string;
  username: string;
  display_name: string | null;
}

export interface UsernameCheck {
  username: string;
  available: boolean;
  /** Null when available. A form that refuses a name without saying why makes
   *  people guess. */
  problem: string | null;
}

export interface Me {
  email: string;
  username: string;
  display_name: string | null;
  created_at: string | null;
  verified: boolean;
}

export interface SignUpResult {
  email: string;
  delivery: "email" | "log";
  message: string;
}

export async function signUp(
  email: string, username: string, password: string,
): Promise<SignUpResult> {
  const { data } = await apiClient.post<SignUpResult>("/auth/signup", {
    email, username, password,
  });
  return data;
}

/** `identifier` is an email address or a username; the server decides which by
 *  whether it contains an "@", which is safe because usernames cannot. */
export async function signInWithPassword(
  identifier: string, password: string,
): Promise<SessionResult> {
  const { data } = await apiClient.post<SessionResult>("/auth/signin", {
    identifier, password,
  });
  return data;
}

export async function checkUsername(username: string): Promise<UsernameCheck> {
  const { data } = await apiClient.get<UsernameCheck>("/auth/username-available", {
    params: { username },
  });
  return data;
}

export async function requestCode(email: string): Promise<CodeRequestResult> {
  const { data } = await apiClient.post<CodeRequestResult>("/auth/request-code", { email });
  return data;
}

export async function verifyCode(email: string, code: string): Promise<SessionResult> {
  const { data } = await apiClient.post<SessionResult>("/auth/verify", { email, code });
  return data;
}

export async function fetchMe(): Promise<Me> {
  const { data } = await apiClient.get<Me>("/auth/me");
  return data;
}

export async function logout(): Promise<void> {
  await apiClient.post("/auth/logout");
}

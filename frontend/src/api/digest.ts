import { apiClient } from "./client";

export type DigestFrequency = "off" | "daily" | "weekly";

export interface DigestSetting {
  frequency: DigestFrequency;
  verified: boolean;
  message: string;
}

export async function fetchDigestSetting(): Promise<DigestSetting> {
  const { data } = await apiClient.get<DigestSetting>("/digest");
  return data;
}

export async function setDigestSetting(frequency: DigestFrequency): Promise<DigestSetting> {
  const { data } = await apiClient.put<DigestSetting>("/digest", { frequency });
  return data;
}

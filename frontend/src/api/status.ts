import { apiClient } from "./client";
import type { SystemStatus } from "../types/models";

export async function fetchSystemStatus(): Promise<SystemStatus> {
  const { data } = await apiClient.get<SystemStatus>("/status");
  return data;
}

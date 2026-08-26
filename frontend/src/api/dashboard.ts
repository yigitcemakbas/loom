import { apiClient } from "./client";
import type { DashboardResponse } from "../types/models";

export async function fetchDashboard(): Promise<DashboardResponse> {
  const { data } = await apiClient.get<DashboardResponse>("/dashboard");
  return data;
}

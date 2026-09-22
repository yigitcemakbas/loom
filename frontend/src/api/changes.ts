import { apiClient } from "./client";
import type { Changes } from "../types/models";

export async function fetchChanges(days = 7): Promise<Changes> {
  const { data } = await apiClient.get<Changes>("/changes", { params: { days } });
  return data;
}

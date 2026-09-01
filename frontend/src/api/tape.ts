import { apiClient } from "./client";
import type { TapeItem } from "../types/models";

export async function fetchTape(): Promise<TapeItem[]> {
  const { data } = await apiClient.get<TapeItem[]>("/tape");
  return data;
}

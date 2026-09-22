import { apiClient } from "./client";
import type { ExposureGraph } from "../types/models";

export async function fetchExposure(): Promise<ExposureGraph> {
  const { data } = await apiClient.get<ExposureGraph>("/exposure");
  return data;
}

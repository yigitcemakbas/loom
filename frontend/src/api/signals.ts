import { apiClient } from "./client";
import type { AnalysisTriggerResponse, SentimentPoint, Signal, SignalType } from "../types/models";

export interface SignalFilters {
  ticker?: string;
  signal_type?: SignalType;
  min_confidence?: number;
  unreviewed_only?: boolean;
  limit?: number;
}

export async function fetchSignals(filters: SignalFilters = {}): Promise<Signal[]> {
  const { data } = await apiClient.get<Signal[]>("/signals", { params: filters });
  return data;
}

export async function fetchSentimentSeries(ticker: string): Promise<SentimentPoint[]> {
  const { data } = await apiClient.get<SentimentPoint[]>(`/companies/${ticker}/sentiment`);
  return data;
}

export async function triggerAnalysis(ticker: string): Promise<AnalysisTriggerResponse> {
  const { data } = await apiClient.post<AnalysisTriggerResponse>(`/admin/analyze/${ticker}`);
  return data;
}

export async function annotateSignal(id: string, note: string): Promise<Signal> {
  const { data } = await apiClient.post<Signal>(`/signals/${id}/note`, { note });
  return data;
}

export async function dismissSignal(id: string): Promise<Signal> {
  const { data } = await apiClient.post<Signal>(`/signals/${id}/dismiss`);
  return data;
}

import { downloadCsv, http } from './client';
import type { MarketCompareResult, MarketDraftSummary, MarketVersionDetail, MarketVersionSummary } from '../types/models';

export const versionsApi = {
  list: () => http.get<{ items: MarketVersionSummary[]; draft: MarketDraftSummary | null }>('/market-versions'),
  draft: () => http.get<MarketVersionDetail>('/market-versions/draft'),
  detail: (id: number) => http.get<MarketVersionDetail>(`/market-versions/${id}`),
  compare: (fromId: number, toId: number) =>
    http.get<MarketCompareResult>(`/market-versions/compare?from_id=${fromId}&to_id=${toId}`),
  publish: (payload: { title?: string; note?: string }) =>
    http.post<MarketVersionDetail>('/market-versions/publish', payload),
  retract: (id: number, note?: string) =>
    http.post<MarketVersionSummary>(`/market-versions/${id}/retract`, { note }),
  downloadCsv: (versionNo: string, id: number) =>
    downloadCsv(`/market-versions/${id}/csv`, `rentlens-market-${versionNo}.csv`),
};

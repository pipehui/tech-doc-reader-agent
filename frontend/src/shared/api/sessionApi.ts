import type { TenantScope } from "../../types";
import {
  jsonClient,
  type FetchJsonOptions,
  type JsonClient
} from "./client";
import {
  decodeHistoryResponse,
  decodeLearningOverview,
  decodeSessionState
} from "./contracts";


export function createSessionApi(client: JsonClient = jsonClient) {
  return {
    getSessionState(
      sessionId: string,
      tenant?: Partial<TenantScope>,
      options: FetchJsonOptions = {}
    ) {
      return client.request(
        `/sessions/${encodeURIComponent(sessionId)}/state`,
        { tenant, signal: options.signal, decode: decodeSessionState }
      );
    },

    getSessionHistory(
      sessionId: string,
      tenant?: Partial<TenantScope>,
      options: FetchJsonOptions = {}
    ) {
      return client.request(
        `/sessions/${encodeURIComponent(sessionId)}/history?include_tools=true`,
        { tenant, signal: options.signal, decode: decodeHistoryResponse }
      );
    },

    getLearningOverview(
      tenant?: Partial<TenantScope>,
      options: FetchJsonOptions = {}
    ) {
      return client.request(
        "/learning/overview",
        { tenant, signal: options.signal, decode: decodeLearningOverview }
      );
    }
  };
}


export const sessionApi = createSessionApi();
export const getSessionState = sessionApi.getSessionState;
export const getSessionHistory = sessionApi.getSessionHistory;
export const getLearningOverview = sessionApi.getLearningOverview;

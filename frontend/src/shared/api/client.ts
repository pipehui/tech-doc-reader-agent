import { normalizeTenant } from "../../tenant";
import type { TenantScope } from "../../types";


export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export interface FetchJsonOptions {
  signal?: AbortSignal;
}

export type JsonDecoder<T> = (payload: unknown) => T;

export interface JsonRequestOptions<T> extends FetchJsonOptions {
  tenant?: Partial<TenantScope>;
  decode: JsonDecoder<T>;
}

export interface JsonClient {
  request<T>(path: string, options: JsonRequestOptions<T>): Promise<T>;
}

export class HttpError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(status: number, message: string, payload: unknown) {
    super(`${status} ${message}`);
    this.name = "HttpError";
    this.status = status;
    this.payload = payload;
  }
}

export class ApiContractError extends Error {
  readonly path: string;

  constructor(path: string, message: string) {
    super(`API contract violation for ${path}: ${message}`);
    this.name = "ApiContractError";
    this.path = path;
  }
}


export function tenantHeaders(tenant?: Partial<TenantScope>) {
  const resolved = normalizeTenant(tenant);
  return {
    "x-user-id": resolved.user_id,
    "x-namespace": resolved.namespace
  };
}


export function buildTenantUrl(
  path: string,
  tenant?: Partial<TenantScope>,
  baseUrl = API_BASE
) {
  const [pathname, query = ""] = path.split("?");
  const params = new URLSearchParams(query);
  const resolved = normalizeTenant(tenant);
  params.set("user_id", resolved.user_id);
  params.set("namespace", resolved.namespace);
  const search = params.toString();
  return `${baseUrl}${pathname}${search ? `?${search}` : ""}`;
}


function validationDetail(payload: unknown) {
  if (!Array.isArray(payload)) return null;
  const messages = payload
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const message = (item as Record<string, unknown>).msg;
      return typeof message === "string" ? message : null;
    })
    .filter((item): item is string => Boolean(item));
  return messages.length ? messages.join("; ") : null;
}


function payloadMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    const record = payload as Record<string, unknown>;
    if (typeof record.message === "string" && record.message.trim()) {
      return record.message.trim();
    }
    if (typeof record.detail === "string" && record.detail.trim()) {
      return record.detail.trim();
    }
    const detail = validationDetail(record.detail);
    if (detail) return detail;
    if (typeof record.error === "string" && record.error.trim()) {
      return record.error.trim();
    }
  }
  if (typeof payload === "string" && payload.trim()) return payload.trim();
  return fallback;
}


async function responsePayload(response: Response) {
  const text = await response.text();
  if (!text.trim()) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    const normalized = text.trim();
    if (
      normalized.length > 500
      || /<!doctype|<html|<body/i.test(normalized)
    ) {
      return null;
    }
    return normalized;
  }
}


const browserFetch: typeof fetch = (input, init) => globalThis.fetch(input, init);


export function createJsonClient({
  baseUrl = API_BASE,
  fetchImpl = browserFetch
}: {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
} = {}): JsonClient {
  return {
    async request<T>(path: string, options: JsonRequestOptions<T>) {
      const response = await fetchImpl(
        buildTenantUrl(path, options.tenant, baseUrl),
        {
          headers: {
            Accept: "application/json",
            ...tenantHeaders(options.tenant)
          },
          signal: options.signal
        }
      );
      if (!response.ok) {
        const payload = await responsePayload(response);
        throw new HttpError(
          response.status,
          payloadMessage(payload, response.statusText || "Request failed"),
          payload
        );
      }

      let payload: unknown;
      try {
        payload = await response.json() as unknown;
      } catch (error) {
        throw new ApiContractError(
          path,
          `response is not valid JSON (${error instanceof Error ? error.message : String(error)})`
        );
      }

      try {
        return options.decode(payload);
      } catch (error) {
        if (error instanceof ApiContractError) throw error;
        throw new ApiContractError(
          path,
          error instanceof Error ? error.message : String(error)
        );
      }
    }
  };
}


export const jsonClient = createJsonClient();

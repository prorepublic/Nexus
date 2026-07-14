/**
 * Typed client for the Nexus control plane API.
 *
 * Base URL comes from NEXT_PUBLIC_NEXUS_API_URL and defaults to
 * http://localhost:8400.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_NEXUS_API_URL ?? "http://localhost:8400";

/* ---------------------------------- types --------------------------------- */

export type GoalStatus =
  | "draft"
  | "planning"
  | "ready"
  | "executing"
  | "blocked"
  | "review"
  | "completed"
  | "failed"
  | "cancelled";

export type GoalPriority = "low" | "normal" | "high";

export type Goal = {
  id: string;
  title: string;
  description: string;
  status: GoalStatus;
  priority: GoalPriority;
  repository: string | null;
  requested_worker: string | null;
  autonomy: string;
  created_at: string;
  updated_at: string;
};

export type TaskStatus =
  | "pending"
  | "ready"
  | "queued"
  | "running"
  | "validating"
  | "repairing"
  | "review"
  | "blocked"
  | "completed"
  | "failed"
  | "cancelled";

export type TaskRisk = "low" | "medium" | "high";

export type Task = {
  id: string;
  title: string;
  status: TaskStatus;
  worker: string | null;
  reviewer: string | null;
  risk: TaskRisk;
  attempt_count: number;
  branch: string | null;
  created_at: string;
};

export type TaskWithGoal = Task & {
  goal_id: string;
  goal_title: string;
};

export type GoalDetail = Goal & {
  tasks: Task[];
};

export type RunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "timed_out";

export type Run = {
  id: string;
  task_id: string;
  task_title: string;
  worker: string;
  status: RunStatus;
  started_at: string | null;
  finished_at: string | null;
  exit_summary: string | null;
};

export type RunEvent = {
  ts: string;
  type: string;
  status: string | null;
  message: string;
};

export type RunDetail = Run & {
  events: RunEvent[];
};

export type ApprovalState = "pending" | "approved" | "denied";

export type Approval = {
  id: string;
  kind: string;
  description: string;
  risk: string;
  requested_at: string;
  state: ApprovalState;
};

export type Health = {
  status: "ok";
  version: string;
  database: "ok" | "unavailable";
};

export type WorkerStatus = {
  name: string;
  provider: string;
  available: boolean;
  version: string | null;
  authenticated: boolean | null;
  detail: string;
};

export type CostMode = {
  paid_apis_enabled: false;
  description: string;
};

export type SystemStatus = {
  version: string;
  database: string;
  cost_mode: CostMode;
  workers: WorkerStatus[];
};

export type RequestedWorker = "claude-code" | "codex-cli" | "fake" | null;

export type CreateGoalInput = {
  title: string;
  description: string;
  repository?: string;
  acceptance_criteria?: string[];
  constraints?: string[];
  requested_worker?: RequestedWorker;
  priority?: GoalPriority;
  autonomy?: "manual" | "bounded";
};

/* --------------------------------- errors --------------------------------- */

/** The control plane responded, but with a non-2xx status. */
export class NexusApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "NexusApiError";
    this.status = status;
  }
}

/** The control plane could not be reached at all. */
export class NexusUnreachableError extends Error {
  readonly url: string;

  constructor(url: string) {
    super(`Control plane unreachable at ${url}`);
    this.name = "NexusUnreachableError";
    this.url = url;
  }
}

/* --------------------------------- client --------------------------------- */

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new NexusUnreachableError(API_BASE);
  }

  if (!res.ok) {
    let message = `Request to ${path} failed with status ${res.status}`;
    try {
      const body: unknown = await res.json();
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        body.detail !== undefined
      ) {
        const detail = (body as { detail: unknown }).detail;
        message =
          typeof detail === "string" ? detail : JSON.stringify(detail);
      }
    } catch {
      // Non-JSON error body; keep the default message.
    }
    throw new NexusApiError(message, res.status);
  }

  return (await res.json()) as T;
}

export const api = {
  health: (): Promise<Health> => request<Health>("/health"),

  systemStatus: (): Promise<SystemStatus> =>
    request<SystemStatus>("/api/system/status"),

  listGoals: (limit = 20): Promise<{ items: Goal[] }> =>
    request<{ items: Goal[] }>(`/api/goals?limit=${limit}`),

  getGoal: (id: string): Promise<GoalDetail> =>
    request<GoalDetail>(`/api/goals/${encodeURIComponent(id)}`),

  createGoal: (input: CreateGoalInput): Promise<Goal> =>
    request<Goal>("/api/goals", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  cancelGoal: (id: string): Promise<{ ok: boolean }> =>
    request<{ ok: boolean }>(
      `/api/goals/${encodeURIComponent(id)}/cancel`,
      { method: "POST" },
    ),

  listTasks: (
    status?: TaskStatus,
    limit = 50,
  ): Promise<{ items: TaskWithGoal[] }> => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    params.set("limit", String(limit));
    return request<{ items: TaskWithGoal[] }>(`/api/tasks?${params}`);
  },

  listRuns: (limit = 50): Promise<{ items: Run[] }> =>
    request<{ items: Run[] }>(`/api/runs?limit=${limit}`),

  getRun: (id: string): Promise<RunDetail> =>
    request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),

  cancelRun: (id: string): Promise<{ ok: boolean }> =>
    request<{ ok: boolean }>(
      `/api/runs/${encodeURIComponent(id)}/cancel`,
      { method: "POST" },
    ),

  listApprovals: (
    state: ApprovalState = "pending",
  ): Promise<{ items: Approval[] }> =>
    request<{ items: Approval[] }>(`/api/approvals?state=${state}`),

  decideApproval: (
    id: string,
    decision: "approved" | "denied",
    note?: string,
  ): Promise<unknown> =>
    request<unknown>(
      `/api/approvals/${encodeURIComponent(id)}/decision`,
      {
        method: "POST",
        body: JSON.stringify(note ? { decision, note } : { decision }),
      },
    ),
};

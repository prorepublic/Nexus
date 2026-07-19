/**
 * Typed client for the Nexus control plane API.
 *
 * All requests go through the same-origin proxy at /api/nexus (ADR-013): the
 * proxy attaches the local-owner credential server-side, so the token never
 * appears in browser JavaScript and no cross-origin request is ever made.
 * Server-side callers (server actions) reach the proxy via this app's own
 * origin.
 */

const SITE_ORIGIN =
  process.env.NEXT_PUBLIC_SITE_ORIGIN ?? "http://localhost:3400";

export const API_BASE =
  typeof window === "undefined" ? `${SITE_ORIGIN}/api/nexus` : "/api/nexus";

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
  review_verdict: string | null;
  risk: TaskRisk;
  attempt_count: number;
  branch: string | null;
  created_at: string;
};

export type ValidationResultStatus =
  | "passed"
  | "failed"
  | "skipped"
  | "blocked"
  | "error";

export type ValidationResult = {
  kind: string;
  status: ValidationResultStatus;
  summary: string;
  exit_code: number | null;
  duration_ms: number | null;
};

export type FindingSeverity = "critical" | "high" | "medium" | "low" | "info";

export type Finding = {
  id: string;
  severity: FindingSeverity;
  category: string;
  description: string;
  file: string | null;
  line: string | null;
  recommendation: string;
  blocking: boolean;
  resolved: boolean;
  source: string;
  reviewer: string;
};

export type TaskDetail = Task & {
  goal_id?: string;
  instruction: string;
  worktree_path: string | null;
  validation_results: ValidationResult[];
  findings: Finding[];
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
  goal_id?: string | null;
  task_id?: string | null;
};

export type TrustLevel =
  | "untrusted"
  | "reviewed"
  | "trusted-local"
  | "trusted-owner-approved";

export type Repository = {
  id: string;
  name: string;
  local_path: string | null;
  github_slug: string | null;
  default_branch: string;
  trust_level: TrustLevel;
  onboarded: boolean;
  languages: string[];
  validation_kinds: string[];
};

export type GoalPlan = {
  id: string;
  goal_id: string;
  objective: string;
  assumptions: string[];
  risks: string[];
  affected_components: string[];
  validation_plan: string[];
  planner: string;
  proposed_solution: string;
};

export type PullRequest = {
  id: string;
  goal_id: string;
  repository: string;
  number: number | null;
  url: string | null;
  branch: string;
  state: string;
  updated_at: string;
};

export type FeedbackImportResult = {
  fetched: number;
  actionable: number;
  ignored: number;
  duplicates: number;
  repair_tasks: string[];
};

export type Settings = {
  review_policy: string;
  planner_mode: string;
  max_repair_attempts: number;
  task_timeout_seconds: number;
  lease_seconds: number;
  cost_mode: CostMode;
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

export type PlanMode = "auto" | "live" | "deterministic";

export type CreateGoalInput = {
  title: string;
  description: string;
  repository?: string;
  acceptance_criteria?: string[];
  constraints?: string[];
  requested_worker?: RequestedWorker;
  priority?: GoalPriority;
  autonomy?: "manual" | "bounded";
  plan_mode?: PlanMode;
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
  const method = (init?.method ?? "GET").toUpperCase();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        // State-changing requests must identify the client or the control
        // plane rejects them with 403.
        ...(method === "POST" ? { "X-Nexus-Client": "dashboard" } : {}),
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

  getTask: (id: string): Promise<TaskDetail> =>
    request<TaskDetail>(`/api/tasks/${encodeURIComponent(id)}`),

  retryTask: (id: string): Promise<{ ok: boolean }> =>
    request<{ ok: boolean }>(
      `/api/tasks/${encodeURIComponent(id)}/retry`,
      { method: "POST" },
    ),

  listRepositories: (): Promise<{ items: Repository[] }> =>
    request<{ items: Repository[] }>("/api/repositories"),

  registerRepository: (input: {
    source: string;
    name?: string;
  }): Promise<Repository> =>
    request<Repository>("/api/repositories", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  setRepositoryTrust: (id: string, level: TrustLevel): Promise<Repository> =>
    request<Repository>(
      `/api/repositories/${encodeURIComponent(id)}/trust`,
      {
        method: "POST",
        body: JSON.stringify({ level }),
      },
    ),

  getGoalPlan: (id: string): Promise<GoalPlan> =>
    request<GoalPlan>(`/api/goals/${encodeURIComponent(id)}/plan`),

  approveGoalPlan: (id: string): Promise<{ ok: boolean }> =>
    request<{ ok: boolean }>(
      `/api/goals/${encodeURIComponent(id)}/approve-plan`,
      { method: "POST" },
    ),

  listPullRequests: (): Promise<{ items: PullRequest[] }> =>
    request<{ items: PullRequest[] }>("/api/pull-requests"),

  importGoalFeedback: (goalId: string): Promise<FeedbackImportResult> =>
    request<FeedbackImportResult>(
      `/api/goals/${encodeURIComponent(goalId)}/feedback/import`,
      { method: "POST" },
    ),

  getSettings: (): Promise<Settings> => request<Settings>("/api/settings"),

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

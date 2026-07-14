import type { CreateGoalInput, GoalPriority, RequestedWorker } from "./api";

export const LIMITS = {
  title: 200,
  description: 4000,
  repository: 500,
  listItem: 500,
  listLength: 20,
} as const;

export const WORKER_OPTIONS: Array<{
  value: "auto" | "claude-code" | "codex-cli" | "fake";
  label: string;
  hint: string;
}> = [
  {
    value: "auto",
    label: "Automatic routing",
    hint: "Let the control plane pick the best available worker.",
  },
  {
    value: "claude-code",
    label: "Claude Code",
    hint: "Route all tasks to the Claude Code CLI.",
  },
  {
    value: "codex-cli",
    label: "Codex CLI",
    hint: "Route all tasks to the Codex CLI.",
  },
  {
    value: "fake",
    label: "Fake worker",
    hint: "Deterministic fake worker for testing the pipeline.",
  },
];

export type GoalFormValues = {
  title: string;
  description: string;
  repository: string;
  acceptance_criteria: string[];
  constraints: string[];
  requested_worker: "auto" | "claude-code" | "codex-cli" | "fake";
  priority: GoalPriority;
  autonomy: "manual" | "bounded";
};

export type GoalFormErrors = Partial<
  Record<"title" | "description" | "repository" | "acceptance_criteria" | "constraints", string>
>;

export function validateGoalForm(values: GoalFormValues): GoalFormErrors {
  const errors: GoalFormErrors = {};

  const title = values.title.trim();
  if (!title) errors.title = "Title is required.";
  else if (title.length > LIMITS.title)
    errors.title = `Title must be ${LIMITS.title} characters or fewer.`;

  const description = values.description.trim();
  if (!description) errors.description = "Description is required.";
  else if (description.length > LIMITS.description)
    errors.description = `Description must be ${LIMITS.description} characters or fewer.`;

  if (values.repository.trim().length > LIMITS.repository)
    errors.repository = `Repository must be ${LIMITS.repository} characters or fewer.`;

  const checkList = (items: string[]): string | null => {
    const nonEmpty = items.map((i) => i.trim()).filter(Boolean);
    if (nonEmpty.length > LIMITS.listLength)
      return `At most ${LIMITS.listLength} entries.`;
    if (nonEmpty.some((i) => i.length > LIMITS.listItem))
      return `Each entry must be ${LIMITS.listItem} characters or fewer.`;
    return null;
  };

  const criteriaError = checkList(values.acceptance_criteria);
  if (criteriaError) errors.acceptance_criteria = criteriaError;
  const constraintsError = checkList(values.constraints);
  if (constraintsError) errors.constraints = constraintsError;

  return errors;
}

/** Convert validated form values into the API payload. */
export function toCreateGoalInput(values: GoalFormValues): CreateGoalInput {
  const requested_worker: RequestedWorker =
    values.requested_worker === "auto" ? null : values.requested_worker;
  const acceptance_criteria = values.acceptance_criteria
    .map((i) => i.trim())
    .filter(Boolean);
  const constraints = values.constraints.map((i) => i.trim()).filter(Boolean);

  return {
    title: values.title.trim(),
    description: values.description.trim(),
    ...(values.repository.trim()
      ? { repository: values.repository.trim() }
      : {}),
    ...(acceptance_criteria.length > 0 ? { acceptance_criteria } : {}),
    ...(constraints.length > 0 ? { constraints } : {}),
    requested_worker,
    priority: values.priority,
    autonomy: values.autonomy,
  };
}

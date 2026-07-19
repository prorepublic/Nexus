"use server";

import { api, NexusApiError, NexusUnreachableError } from "@/lib/api";
import {
  toCreateGoalInput,
  validateGoalForm,
  type GoalFormErrors,
  type GoalFormValues,
} from "@/lib/goalValidation";

export type CreateGoalResult =
  | { ok: true; id: string }
  | { ok: false; fieldErrors: GoalFormErrors; formError: string | null };

/**
 * Server-side validation and submission. The same validation rules run on
 * the client for instant feedback; this action is the authoritative check.
 */
export async function createGoalAction(
  values: GoalFormValues,
): Promise<CreateGoalResult> {
  const fieldErrors = validateGoalForm(values);
  if (Object.keys(fieldErrors).length > 0) {
    return { ok: false, fieldErrors, formError: null };
  }

  try {
    const goal = await api.createGoal(toCreateGoalInput(values));
    return { ok: true, id: goal.id };
  } catch (err) {
    if (err instanceof NexusUnreachableError) {
      return { ok: false, fieldErrors: {}, formError: err.message };
    }
    if (err instanceof NexusApiError) {
      return {
        ok: false,
        fieldErrors: {},
        formError: `The control plane rejected the goal: ${err.message}`,
      };
    }
    return {
      ok: false,
      fieldErrors: {},
      formError: "Unexpected error while creating the goal.",
    };
  }
}

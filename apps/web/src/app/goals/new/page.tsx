"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  LIMITS,
  validateGoalForm,
  WORKER_OPTIONS,
  type GoalFormErrors,
  type GoalFormValues,
} from "@/lib/goalValidation";
import { createGoalAction } from "./actions";
import { Card } from "@/components/Card";

const INITIAL_VALUES: GoalFormValues = {
  title: "",
  description: "",
  repository: "",
  acceptance_criteria: [""],
  constraints: [""],
  requested_worker: "auto",
  priority: "normal",
  autonomy: "manual",
};

const inputClass =
  "w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:border-sky-500 focus:outline-none";
const labelClass = "mb-1.5 block text-sm font-medium text-zinc-300";
const errorClass = "mt-1 text-xs text-red-400";

function DynamicList({
  idPrefix,
  label,
  hint,
  values,
  onChange,
  error,
  addLabel,
}: {
  idPrefix: string;
  label: string;
  hint: string;
  values: string[];
  onChange: (next: string[]) => void;
  error?: string;
  addLabel: string;
}) {
  return (
    <fieldset>
      <legend className={labelClass}>{label}</legend>
      <p className="mb-2 text-xs text-zinc-500">{hint}</p>
      <div className="space-y-2">
        {values.map((value, index) => (
          <div key={index} className="flex gap-2">
            <label htmlFor={`${idPrefix}-${index}`} className="sr-only">
              {label} entry {index + 1}
            </label>
            <input
              id={`${idPrefix}-${index}`}
              type="text"
              value={value}
              maxLength={LIMITS.listItem}
              onChange={(e) => {
                const next = [...values];
                next[index] = e.target.value;
                onChange(next);
              }}
              className={inputClass}
            />
            <button
              type="button"
              onClick={() => {
                const next = values.filter((_, i) => i !== index);
                onChange(next.length > 0 ? next : [""]);
              }}
              className="shrink-0 rounded-md border border-zinc-700 px-3 text-xs text-zinc-400 hover:bg-zinc-800"
            >
              Remove
            </button>
          </div>
        ))}
      </div>
      {error ? <p className={errorClass}>{error}</p> : null}
      <button
        type="button"
        onClick={() => onChange([...values, ""])}
        disabled={values.length >= LIMITS.listLength}
        className="mt-2 rounded-md border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
      >
        {addLabel}
      </button>
    </fieldset>
  );
}

export default function NewGoalPage() {
  const router = useRouter();
  const [values, setValues] = useState<GoalFormValues>(INITIAL_VALUES);
  const [errors, setErrors] = useState<GoalFormErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = <K extends keyof GoalFormValues>(
    key: K,
    value: GoalFormValues[K],
  ) => setValues((prev) => ({ ...prev, [key]: value }));

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);

    const clientErrors = validateGoalForm(values);
    setErrors(clientErrors);
    if (Object.keys(clientErrors).length > 0) return;

    setSubmitting(true);
    try {
      const result = await createGoalAction(values);
      if (result.ok) {
        router.push(`/goals/${result.id}`);
        return;
      }
      setErrors(result.fieldErrors);
      setFormError(result.formError);
    } catch {
      setFormError("Unexpected error while submitting the goal.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <Link
          href="/goals"
          className="text-xs text-zinc-500 hover:text-zinc-300"
        >
          Goals
        </Link>
        <h1 className="mt-1 text-lg font-semibold text-zinc-100">New goal</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Describe the outcome you want. The control plane plans tasks and
          routes them to workers.
        </p>
      </div>

      <Card>
        <form onSubmit={(e) => void handleSubmit(e)} noValidate>
          <div className="space-y-6">
            {formError ? (
              <div
                role="alert"
                className="rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300"
              >
                {formError}
              </div>
            ) : null}

            <div>
              <label htmlFor="goal-title" className={labelClass}>
                Title <span className="text-red-400">*</span>
              </label>
              <input
                id="goal-title"
                type="text"
                required
                maxLength={LIMITS.title}
                value={values.title}
                onChange={(e) => set("title", e.target.value)}
                placeholder="Add rate limiting to the ingestion API"
                className={inputClass}
                aria-invalid={Boolean(errors.title)}
              />
              {errors.title ? (
                <p className={errorClass}>{errors.title}</p>
              ) : null}
            </div>

            <div>
              <label htmlFor="goal-description" className={labelClass}>
                Desired outcome <span className="text-red-400">*</span>
              </label>
              <textarea
                id="goal-description"
                required
                rows={5}
                maxLength={LIMITS.description}
                value={values.description}
                onChange={(e) => set("description", e.target.value)}
                placeholder="What should be true when this goal is done?"
                className={inputClass}
                aria-invalid={Boolean(errors.description)}
              />
              {errors.description ? (
                <p className={errorClass}>{errors.description}</p>
              ) : null}
            </div>

            <div>
              <label htmlFor="goal-repository" className={labelClass}>
                Repository <span className="text-zinc-600">(optional)</span>
              </label>
              <input
                id="goal-repository"
                type="text"
                maxLength={LIMITS.repository}
                value={values.repository}
                onChange={(e) => set("repository", e.target.value)}
                placeholder="/path/to/repo or org/repo"
                className={inputClass}
                aria-invalid={Boolean(errors.repository)}
              />
              {errors.repository ? (
                <p className={errorClass}>{errors.repository}</p>
              ) : null}
            </div>

            <DynamicList
              idPrefix="criterion"
              label="Acceptance criteria"
              hint="Conditions that must hold for the goal to count as done."
              values={values.acceptance_criteria}
              onChange={(next) => set("acceptance_criteria", next)}
              error={errors.acceptance_criteria}
              addLabel="Add criterion"
            />

            <DynamicList
              idPrefix="constraint"
              label="Constraints"
              hint="Boundaries the workers must respect (files, approach, scope)."
              values={values.constraints}
              onChange={(next) => set("constraints", next)}
              error={errors.constraints}
              addLabel="Add constraint"
            />

            <fieldset>
              <legend className={labelClass}>Worker</legend>
              <div className="space-y-2">
                {WORKER_OPTIONS.map((option) => (
                  <label
                    key={option.value}
                    className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 ${
                      values.requested_worker === option.value
                        ? "border-sky-500/50 bg-sky-500/5"
                        : "border-zinc-800 hover:border-zinc-700"
                    }`}
                  >
                    <input
                      type="radio"
                      name="requested_worker"
                      value={option.value}
                      checked={values.requested_worker === option.value}
                      onChange={() => set("requested_worker", option.value)}
                      className="mt-1 accent-sky-500"
                    />
                    <span>
                      <span className="block text-sm text-zinc-200">
                        {option.label}
                      </span>
                      <span className="block text-xs text-zinc-500">
                        {option.hint}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label htmlFor="goal-priority" className={labelClass}>
                  Priority
                </label>
                <select
                  id="goal-priority"
                  value={values.priority}
                  onChange={(e) =>
                    set("priority", e.target.value as GoalFormValues["priority"])
                  }
                  className={inputClass}
                >
                  <option value="low">Low</option>
                  <option value="normal">Normal</option>
                  <option value="high">High</option>
                </select>
              </div>
              <div>
                <label htmlFor="goal-autonomy" className={labelClass}>
                  Autonomy
                </label>
                <select
                  id="goal-autonomy"
                  value={values.autonomy}
                  onChange={(e) =>
                    set("autonomy", e.target.value as GoalFormValues["autonomy"])
                  }
                  className={inputClass}
                >
                  <option value="manual">Manual approval per task</option>
                  <option value="bounded">Bounded autonomy</option>
                </select>
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 border-t border-zinc-800 pt-4">
              <Link
                href="/goals"
                className="rounded-md border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-800"
              >
                Cancel
              </Link>
              <button
                type="submit"
                disabled={submitting}
                className="rounded-md bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-900 hover:bg-white disabled:opacity-50"
              >
                {submitting ? "Creating…" : "Create goal"}
              </button>
            </div>
          </div>
        </form>
      </Card>
    </div>
  );
}

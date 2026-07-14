import { TaskDetailView } from "./TaskDetailView";

export default async function TaskDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <TaskDetailView id={id} />;
}

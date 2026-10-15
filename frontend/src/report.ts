import { apiText } from "./api";

/** Download only while the caller still belongs to the initiating auth session. */
export async function downloadRunReport(
  runId: string,
  isCurrent: () => boolean,
): Promise<boolean> {
  const markdown = await apiText(`/runs/${encodeURIComponent(runId)}/report`);
  if (!isCurrent()) return false;

  const report = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(report);
  const link = document.createElement("a");
  try {
    if (!isCurrent()) return false;
    link.href = url;
    link.download = `splash-analysis-${runId.replace(/[^a-zA-Z0-9_-]/g, "_")}.md`;
    link.hidden = true;
    document.body.appendChild(link);
    link.click();
    return true;
  } finally {
    link.remove();
    // Let the browser consume the click before releasing the in-memory file.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

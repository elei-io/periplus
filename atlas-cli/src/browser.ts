import { execFile } from "node:child_process";

export function resourceUrl(webUrl: string, path: string): string {
  return new URL(path.replace(/^\//, ""), `${webUrl.replace(/\/$/, "")}/`)
    .toString();
}

export async function openBrowser(url: string): Promise<void> {
  const command =
    process.platform === "darwin"
      ? "open"
      : process.platform === "win32"
        ? "cmd"
        : "xdg-open";
  const args =
    process.platform === "win32"
      ? ["/c", "start", "", url]
      : [url];
  await new Promise<void>((resolve, reject) => {
    execFile(command, args, (error) => {
      if (error) reject(new Error(`Could not open Atlas Web: ${error.message}`));
      else resolve();
    });
  });
}

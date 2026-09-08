// One contiguous replacement keeps review bounded even for large SQL drafts.
export function sqlDiff(before: string, after: string): string {
  const left = before.split("\n"), right = after.split("\n")
  let start = 0
  while (start < left.length && start < right.length && left[start] === right[start]) start++
  let end = 0
  while (end < left.length - start && end < right.length - start && left[left.length - 1 - end] === right[right.length - 1 - end]) end++
  return [
    ...left.slice(0, start).map(line => `  ${line}`),
    ...left.slice(start, left.length - end).map(line => `- ${line}`),
    ...right.slice(start, right.length - end).map(line => `+ ${line}`),
    ...(end ? left.slice(-end).map(line => `  ${line}`) : []),
  ].join("\n")
}

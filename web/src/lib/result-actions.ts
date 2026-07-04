export function resultActionHref(pathname: string, url: string) {
  const params = new URLSearchParams({ url })

  return `${pathname}?${params.toString()}`
}

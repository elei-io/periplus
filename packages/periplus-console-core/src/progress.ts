export interface ProgressFrame {
  symbol: string
  elapsedSeconds: string
}

const FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

export function startProgress(
  render: (frame: ProgressFrame) => void,
  clear: () => void,
  delayMilliseconds = 150,
): { stop(): void } {
  const startedAt = performance.now()
  let frame = 0
  let visible = false
  let interval: ReturnType<typeof setInterval> | undefined
  const draw = () => {
    visible = true
    render({
      symbol: FRAMES[frame++ % FRAMES.length]!,
      elapsedSeconds: ((performance.now() - startedAt) / 1_000).toFixed(1),
    })
  }
  const delay = setTimeout(() => {
    draw()
    interval = setInterval(draw, 80)
  }, delayMilliseconds)
  return {
    stop() {
      clearTimeout(delay)
      if (interval) clearInterval(interval)
      if (visible) clear()
      visible = false
    },
  }
}

export function speedometerScale(rate: number | undefined): number {
  const target = Math.max(5, (rate ?? 0) * 1.4)
  const power = 10 ** Math.floor(Math.log10(target))
  return ([1, 2, 5, 10].find(value => value * power >= target) ?? 10) * power
}

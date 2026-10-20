export function formatNumber(value: number, locale: string): string {
  const options: Intl.NumberFormatOptions =
    Math.abs(value) >= 10_000
      ? { notation: 'compact', maximumFractionDigits: 1 }
      : { maximumFractionDigits: 0 }
  return new Intl.NumberFormat(locale, options).format(value)
}

export function formatPercent(
  numerator: number,
  denominator: number,
  locale: string,
): string | null {
  if (denominator === 0) return null
  return new Intl.NumberFormat(locale, {
    style: 'percent',
    maximumFractionDigits: 0,
  }).format(numerator / denominator)
}

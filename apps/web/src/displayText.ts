/**
 * Bank descriptions sometimes arrive with numeric HTML character references still encoded
 * (for example `Spotif&#x79;`). Decode them for reading; React renders the result as text, so
 * markup-like names remain inert rather than becoming HTML.
 */
export function displayText(value: string): string {
  return value.replace(/&#(?:x([\da-f]{1,6})|(\d{1,7}));/gi, (reference, hex, decimal) => {
    const point = Number.parseInt(hex ?? decimal, hex ? 16 : 10);
    if (
      !Number.isInteger(point) || point <= 0 || point > 0x10ffff ||
      (point >= 0xd800 && point <= 0xdfff)
    ) return reference;
    return String.fromCodePoint(point);
  });
}
